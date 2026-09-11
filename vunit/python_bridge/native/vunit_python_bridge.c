/*
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this file,
 * You can obtain one at http://mozilla.org/MPL/2.0/.
 *
 * Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com
 *
 * Native bridge between VHDL (NVC/GHDL VHPIDIRECT) and an embedded CPython
 * interpreter. Private implementation detail of python_pkg; the only public
 * VHDL operations are python_execute and python_call.
 *
 * ABI rules, chosen to be identical for NVC and GHDL on all platforms:
 *   - VHDL integer  <-> int32_t, VHDL real <-> double.
 *   - Booleans are passed as integers (0/1), never as VHDL boolean.
 *   - Strings and integer vectors only cross the boundary as *constrained*
 *     chunks (plain pointers) with explicit offsets/lengths. Unconstrained
 *     arrays (fat pointers) are simulator specific and are never used.
 *   - Every operation that can fail returns a status (0 = ok, 1 = error).
 *     The error text is retrieved with vpy_error_length/vpy_error_read.
 *
 * Python objects are only touched with the GIL held. The interpreter is
 * initialized lazily on first use and is kept alive for the lifetime of the
 * simulator process (it is never finalized).
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#include <windows.h>
#define VPY_EXPORT __declspec(dllexport)
#else
#include <dlfcn.h>
#define VPY_EXPORT __attribute__((visibility("default")))
#endif

#ifdef Py_GIL_DISABLED
#error "The VUnit Python bridge does not support free-threaded CPython builds"
#endif

#define VPY_OK 0
#define VPY_ERROR 1

#define VPY_CONFIG_FILE_NAME "vunit_python_bridge.cfg"

/* Result kinds, must match python_pkg and ../runtime.py */
#define VPY_KIND_INTEGER 0
#define VPY_KIND_REAL 1
#define VPY_KIND_BOOLEAN 2
#define VPY_KIND_INTEGER_ARRAY 8
#define VPY_NUM_META 8

/* ------------------------------------------------------------------------ */
/* Process-wide state                                                        */
/* ------------------------------------------------------------------------ */

typedef enum { STATE_UNINITIALIZED, STATE_READY, STATE_FAILED } state_t;

static state_t g_state = STATE_UNINITIALIZED;

/* Error text, UTF-8, owned. Sticky when initialization failed. */
static char *g_error = NULL;
static size_t g_error_length = 0;

/* Raw byte buffer used to transfer strings from VHDL, owned. */
static char *g_buffer = NULL;
static size_t g_buffer_length = 0;
static size_t g_buffer_capacity = 0;

/* Python objects, all strong references, only touched with the GIL held. */
static PyObject *g_runtime = NULL; /* runtime.Runtime instance */
static PyObject *g_args = NULL;    /* list of positional arguments of the next call */
static PyObject *g_pending = NULL; /* bytearray backing the array argument being filled */
static PyObject *g_result = NULL;  /* bytes object holding a string/array result */

/* Cached views into g_pending/g_result. Both objects are owned by us and are
 * never resized while referenced, so the pointers stay valid. */
static char *g_pending_data = NULL;
static size_t g_pending_size = 0;
static size_t g_pending_position = 0;
static const char *g_result_data = NULL;
static size_t g_result_size = 0;

static int32_t g_result_integer = 0;
static double g_result_real = 0.0;
static int32_t g_result_meta[VPY_NUM_META];

/* Configuration read from VPY_CONFIG_FILE_NAME next to this library. */
static char *g_cfg_executable = NULL;
static char *g_cfg_python_dll = NULL;
static char *g_cfg_runtime = NULL;
static char *g_cfg_base_dir = NULL;
static char *g_cfg_prefix = NULL;

/* ------------------------------------------------------------------------ */
/* Error handling                                                            */
/* ------------------------------------------------------------------------ */

static void set_error_bytes(const char *text, size_t length) {
  char *copy = (char *)malloc(length + 1);

  free(g_error);
  g_error = NULL;
  g_error_length = 0;
  if (copy == NULL) {
    return;
  }
  memcpy(copy, text, length);
  copy[length] = '\0';
  g_error = copy;
  g_error_length = length;
}

static void set_error(const char *text) { set_error_bytes(text, strlen(text)); }

static void set_error2(const char *prefix, const char *detail) {
  size_t prefix_length = strlen(prefix);
  size_t detail_length = detail == NULL ? 0 : strlen(detail);
  char *text = (char *)malloc(prefix_length + detail_length + 1);

  if (text == NULL) {
    set_error(prefix);
    return;
  }
  memcpy(text, prefix, prefix_length);
  if (detail_length > 0) {
    memcpy(text + prefix_length, detail, detail_length);
  }
  text[prefix_length + detail_length] = '\0';
  set_error_bytes(text, prefix_length + detail_length);
  free(text);
}

/*
 * Convert the currently raised Python exception into g_error and clear it.
 * Formatting is delegated to the runtime (which hides bridge-internal
 * traceback frames); plain str() and a fixed text are the fallbacks.
 * Must be called with the GIL held.
 */
static void set_error_from_python(void) {
  PyObject *exc;
  PyObject *text = NULL;

#if PY_VERSION_HEX >= 0x030C0000
  exc = PyErr_GetRaisedException();
#else
  {
    PyObject *type, *value, *traceback;
    PyErr_Fetch(&type, &value, &traceback);
    PyErr_NormalizeException(&type, &value, &traceback);
    if (value != NULL && traceback != NULL) {
      PyException_SetTraceback(value, traceback);
    }
    Py_XDECREF(type);
    Py_XDECREF(traceback);
    exc = value;
  }
#endif

  if (exc == NULL) {
    set_error("Unknown Python error (no exception set)");
    return;
  }

  if (g_runtime != NULL) {
    text = PyObject_CallMethod(g_runtime, "format_exception", "O", exc);
    if (text == NULL) {
      PyErr_Clear();
    }
  }
  if (text == NULL) {
    text = PyObject_Str(exc);
    if (text == NULL) {
      PyErr_Clear();
    }
  }

  if (text != NULL && PyUnicode_Check(text)) {
    Py_ssize_t length;
    const char *utf8 = PyUnicode_AsUTF8AndSize(text, &length);
    if (utf8 != NULL) {
      set_error_bytes(utf8, (size_t)length);
    } else {
      PyErr_Clear();
      set_error("Python error (message could not be encoded)");
    }
  } else {
    set_error("Python error (message could not be formatted)");
  }

  Py_XDECREF(text);
  Py_DECREF(exc);
}

/* ------------------------------------------------------------------------ */
/* Configuration                                                             */
/* ------------------------------------------------------------------------ */

/* Return a malloc'ed UTF-8 path of the directory containing this library. */
static char *get_library_directory(void) {
#ifdef _WIN32
  HMODULE module = NULL;
  static wchar_t wpath[32768]; /* static: keep it off the simulator stack */
  DWORD length;
  int utf8_length;
  char *path;
  char *separator;

  if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                              GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                          (LPCWSTR)(void *)&get_library_directory, &module)) {
    return NULL;
  }
  length = GetModuleFileNameW(module, wpath, (DWORD)(sizeof(wpath) / sizeof(wpath[0])));
  if (length == 0 || length >= sizeof(wpath) / sizeof(wpath[0])) {
    return NULL;
  }
  utf8_length = WideCharToMultiByte(CP_UTF8, 0, wpath, (int)length, NULL, 0, NULL, NULL);
  if (utf8_length <= 0) {
    return NULL;
  }
  path = (char *)malloc((size_t)utf8_length + 1);
  if (path == NULL) {
    return NULL;
  }
  WideCharToMultiByte(CP_UTF8, 0, wpath, (int)length, path, utf8_length, NULL, NULL);
  path[utf8_length] = '\0';
  separator = strrchr(path, '\\');
  if (separator == NULL) {
    separator = strrchr(path, '/');
  }
#else
  Dl_info info;
  char *path;
  char *separator;

  if (dladdr((void *)&get_library_directory, &info) == 0 || info.dli_fname == NULL) {
    return NULL;
  }
  path = strdup(info.dli_fname);
  if (path == NULL) {
    return NULL;
  }
  separator = strrchr(path, '/');
#endif
  if (separator == NULL) {
    free(path);
    return NULL;
  }
  *separator = '\0';
  return path;
}

static FILE *open_utf8_path(const char *path) {
#ifdef _WIN32
  int length = MultiByteToWideChar(CP_UTF8, 0, path, -1, NULL, 0);
  wchar_t *wpath;
  FILE *file;

  if (length <= 0) {
    return NULL;
  }
  wpath = (wchar_t *)malloc(sizeof(wchar_t) * (size_t)length);
  if (wpath == NULL) {
    return NULL;
  }
  MultiByteToWideChar(CP_UTF8, 0, path, -1, wpath, length);
  file = _wfopen(wpath, L"rb");
  free(wpath);
  return file;
#else
  return fopen(path, "rb");
#endif
}

/*
 * Read "key=value" lines (UTF-8) from the configuration file written by VUnit
 * next to this library. Returns VPY_ERROR with g_error set on failure.
 */
static int read_config(void) {
  char *directory = get_library_directory();
  char *path;
  size_t directory_length;
  FILE *file;
  char line[8192];

  if (directory == NULL) {
    set_error("Failed to determine the location of the VUnit Python bridge library");
    return VPY_ERROR;
  }

  directory_length = strlen(directory);
  path = (char *)malloc(directory_length + 1 + sizeof(VPY_CONFIG_FILE_NAME));
  if (path == NULL) {
    free(directory);
    set_error("Out of memory");
    return VPY_ERROR;
  }
  memcpy(path, directory, directory_length);
#ifdef _WIN32
  path[directory_length] = '\\';
#else
  path[directory_length] = '/';
#endif
  memcpy(path + directory_length + 1, VPY_CONFIG_FILE_NAME, sizeof(VPY_CONFIG_FILE_NAME));
  free(directory);

  file = open_utf8_path(path);
  if (file == NULL) {
    set_error2("Failed to open VUnit Python bridge configuration file ", path);
    free(path);
    return VPY_ERROR;
  }
  free(path);

  while (fgets(line, (int)sizeof(line), file) != NULL) {
    size_t length = strlen(line);
    char *separator;
    char **target = NULL;

    while (length > 0 && (line[length - 1] == '\n' || line[length - 1] == '\r')) {
      line[--length] = '\0';
    }
    separator = strchr(line, '=');
    if (separator == NULL) {
      continue;
    }
    *separator = '\0';
    if (strcmp(line, "executable") == 0) {
      target = &g_cfg_executable;
    } else if (strcmp(line, "python_dll") == 0) {
      target = &g_cfg_python_dll;
    } else if (strcmp(line, "runtime") == 0) {
      target = &g_cfg_runtime;
    } else if (strcmp(line, "base_dir") == 0) {
      target = &g_cfg_base_dir;
    } else if (strcmp(line, "prefix") == 0) {
      target = &g_cfg_prefix;
    }
    if (target != NULL) {
      free(*target);
      *target = strdup(separator + 1);
    }
  }
  fclose(file);

  if (g_cfg_executable == NULL || g_cfg_runtime == NULL || g_cfg_base_dir == NULL ||
      g_cfg_prefix == NULL) {
    set_error("Incomplete VUnit Python bridge configuration file");
    return VPY_ERROR;
  }
#ifdef _WIN32
  if (g_cfg_python_dll == NULL) {
    set_error("VUnit Python bridge configuration file lacks the Python DLL path");
    return VPY_ERROR;
  }
#endif
  return VPY_OK;
}

/* ------------------------------------------------------------------------ */
/* Interpreter initialization                                                */
/* ------------------------------------------------------------------------ */

/*
 * Make the Python runtime library usable by extension modules.
 *
 * Windows: this library delay-loads pythonXY.dll. Load it by absolute path
 * before the first Python API call so the delay-load helper finds it no
 * matter how the simulator's DLL search path looks.
 *
 * POSIX: simulators dlopen() this library with RTLD_LOCAL, which makes
 * libpython (our dependency) invisible to extension modules such as NumPy
 * that expect the Python symbols to be globally available. Promote libpython
 * to RTLD_GLOBAL.
 */
static int load_python_library(void) {
#ifdef _WIN32
  int length = MultiByteToWideChar(CP_UTF8, 0, g_cfg_python_dll, -1, NULL, 0);
  wchar_t *wpath;
  HMODULE module;

  if (length <= 0) {
    set_error("Invalid Python DLL path in the VUnit Python bridge configuration");
    return VPY_ERROR;
  }
  wpath = (wchar_t *)malloc(sizeof(wchar_t) * (size_t)length);
  if (wpath == NULL) {
    set_error("Out of memory");
    return VPY_ERROR;
  }
  MultiByteToWideChar(CP_UTF8, 0, g_cfg_python_dll, -1, wpath, length);
  module = LoadLibraryExW(wpath, NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
  free(wpath);
  if (module == NULL) {
    char message[64];
    snprintf(message, sizeof(message), " (Windows error %lu)", (unsigned long)GetLastError());
    set_error2("Failed to load the Python DLL ", g_cfg_python_dll);
    set_error2(g_error, message);
    return VPY_ERROR;
  }
  /* Intentionally never freed: the interpreter lives until process exit. */
  return VPY_OK;
#else
  Dl_info info;

  if (dladdr((void *)&Py_InitializeFromConfig, &info) != 0 && info.dli_fname != NULL) {
    /* Intentionally never closed: the interpreter lives until process exit. */
    if (dlopen(info.dli_fname, RTLD_NOW | RTLD_GLOBAL | RTLD_NOLOAD) == NULL) {
      set_error2("Failed to make the Python library globally visible: ", dlerror());
      return VPY_ERROR;
    }
  }
  return VPY_OK;
#endif
}

static int set_config_string(PyConfig *config, wchar_t **field, const char *value) {
  PyStatus status;
#ifdef _WIN32
  int length = MultiByteToWideChar(CP_UTF8, 0, value, -1, NULL, 0);
  wchar_t *wvalue;

  if (length <= 0) {
    set_error("Invalid path in the VUnit Python bridge configuration");
    return VPY_ERROR;
  }
  wvalue = (wchar_t *)malloc(sizeof(wchar_t) * (size_t)length);
  if (wvalue == NULL) {
    set_error("Out of memory");
    return VPY_ERROR;
  }
  MultiByteToWideChar(CP_UTF8, 0, value, -1, wvalue, length);
  status = PyConfig_SetString(config, field, wvalue);
  free(wvalue);
#else
  status = PyConfig_SetBytesString(config, field, value);
#endif
  if (PyStatus_Exception(status)) {
    set_error2("Failed to configure Python: ", status.err_msg);
    return VPY_ERROR;
  }
  return VPY_OK;
}

/* Create the interpreter, if not already done by someone else in this process. */
static int initialize_interpreter(void) {
  PyConfig config;
  PyStatus status;

  if (Py_IsInitialized()) {
    return VPY_OK;
  }

  PyConfig_InitPythonConfig(&config);
  /* The simulator owns the process: no signal handlers, no argv parsing. */
  config.install_signal_handlers = 0;
  config.parse_argv = 0;

  /* Make path configuration behave as if g_cfg_executable (the interpreter
   * that launched VUnit) was started, which selects the same installation,
   * virtual environment and site-packages. */
  if (set_config_string(&config, &config.program_name, g_cfg_executable) != VPY_OK ||
      set_config_string(&config, &config.executable, g_cfg_executable) != VPY_OK) {
    PyConfig_Clear(&config);
    return VPY_ERROR;
  }

  status = Py_InitializeFromConfig(&config);
  PyConfig_Clear(&config);
  if (PyStatus_Exception(status)) {
    set_error2("Failed to initialize Python: ",
               status.err_msg != NULL ? status.err_msg : "unknown error");
    return VPY_ERROR;
  }
  /* This thread now holds the GIL. */
  return VPY_OK;
}

/* Load ../runtime.py and create the Runtime instance. GIL held. */
static int create_runtime(void) {
  static const char *bootstrap =
      "import importlib.util, sys\n"
      "_spec = importlib.util.spec_from_file_location('_vunit_python_runtime', runtime_path)\n"
      "_module = importlib.util.module_from_spec(_spec)\n"
      "sys.modules[_spec.name] = _module\n"
      "_spec.loader.exec_module(_module)\n"
      "runtime = _module.Runtime(base_dir=base_dir, prefix=prefix)\n";
  PyObject *globals = NULL;
  PyObject *value = NULL;
  PyObject *ret = NULL;
  int status = VPY_ERROR;

  globals = PyDict_New();
  if (globals == NULL) {
    goto python_error;
  }
  if (PyDict_SetItemString(globals, "__builtins__", PyEval_GetBuiltins()) < 0) {
    goto python_error;
  }

  value = PyUnicode_DecodeUTF8(g_cfg_runtime, (Py_ssize_t)strlen(g_cfg_runtime), "surrogateescape");
  if (value == NULL || PyDict_SetItemString(globals, "runtime_path", value) < 0) {
    goto python_error;
  }
  Py_CLEAR(value);

  value = PyUnicode_DecodeUTF8(g_cfg_base_dir, (Py_ssize_t)strlen(g_cfg_base_dir), "surrogateescape");
  if (value == NULL || PyDict_SetItemString(globals, "base_dir", value) < 0) {
    goto python_error;
  }
  Py_CLEAR(value);

  value = PyUnicode_DecodeUTF8(g_cfg_prefix, (Py_ssize_t)strlen(g_cfg_prefix), "surrogateescape");
  if (value == NULL || PyDict_SetItemString(globals, "prefix", value) < 0) {
    goto python_error;
  }
  Py_CLEAR(value);

  ret = PyRun_String(bootstrap, Py_file_input, globals, globals);
  if (ret == NULL) {
    goto python_error;
  }

  g_runtime = PyDict_GetItemString(globals, "runtime"); /* borrowed */
  if (g_runtime == NULL) {
    set_error("Failed to create the VUnit Python runtime");
    goto done;
  }
  Py_INCREF(g_runtime);
  status = VPY_OK;
  goto done;

python_error:
  set_error_from_python();

done:
  Py_XDECREF(ret);
  Py_XDECREF(value);
  Py_XDECREF(globals);
  return status;
}

static int initialize(void) {
  int we_initialized;
  PyGILState_STATE gil;

  if (g_state == STATE_READY) {
    return VPY_OK;
  }
  if (g_state == STATE_FAILED) {
    return VPY_ERROR; /* sticky, g_error still holds the reason */
  }
  g_state = STATE_FAILED;

  if (read_config() != VPY_OK || load_python_library() != VPY_OK) {
    return VPY_ERROR;
  }

  we_initialized = !Py_IsInitialized();
  if (we_initialized) {
    if (initialize_interpreter() != VPY_OK) {
      return VPY_ERROR;
    }
  } else {
    gil = PyGILState_Ensure();
  }

  if (create_runtime() != VPY_OK) {
    /* Keep the interpreter; report the error. */
    if (we_initialized) {
      PyEval_SaveThread();
    } else {
      PyGILState_Release(gil);
    }
    return VPY_ERROR;
  }

  if (we_initialized) {
    /* Release the GIL taken by initialization. Every entry point below
     * acquires it with PyGILState_Ensure, from whatever thread the
     * simulator happens to call us on. The thread state is kept forever. */
    PyEval_SaveThread();
  } else {
    PyGILState_Release(gil);
  }

  g_state = STATE_READY;
  return VPY_OK;
}

/* ------------------------------------------------------------------------ */
/* Helpers for entry points                                                  */
/* ------------------------------------------------------------------------ */

/* Python-touching entry points must not run before a successful vpy_begin. */
#define REQUIRE_READY()                                                   \
  do {                                                                    \
    if (g_state != STATE_READY) {                                         \
      if (g_error == NULL) {                                              \
        set_error("Internal error: VUnit Python bridge is not initialized"); \
      }                                                                   \
      return VPY_ERROR;                                                   \
    }                                                                     \
  } while (0)

static void flush_c_streams(void) {
  fflush(stdout);
  fflush(stderr);
}

/* Drop the result of the previous call. GIL held. */
static void clear_result(void) {
  Py_CLEAR(g_result);
  g_result_data = NULL;
  g_result_size = 0;
  g_result_integer = 0;
  g_result_real = 0.0;
  memset(g_result_meta, 0, sizeof(g_result_meta));
}

/* Drop the pending array argument buffer. GIL held. */
static void clear_pending(void) {
  Py_CLEAR(g_pending);
  g_pending_data = NULL;
  g_pending_size = 0;
  g_pending_position = 0;
}

/* Append a new reference to the argument list; steals the reference. GIL held. */
static int append_arg(PyObject *value) {
  int status;

  if (value == NULL) {
    set_error_from_python();
    return VPY_ERROR;
  }
  if (g_args == NULL) {
    Py_DECREF(value);
    set_error("Internal error: argument pushed before vpy_begin");
    return VPY_ERROR;
  }
  status = PyList_Append(g_args, value);
  Py_DECREF(value);
  if (status < 0) {
    set_error_from_python();
    return VPY_ERROR;
  }
  return VPY_OK;
}

/* New reference to the transfer buffer as a str. GIL held. */
static PyObject *buffer_as_str(void) {
  return PyUnicode_DecodeUTF8(g_buffer == NULL ? "" : g_buffer, (Py_ssize_t)g_buffer_length,
                              "surrogateescape");
}

/* ------------------------------------------------------------------------ */
/* Entry points (VHPIDIRECT)                                                 */
/* ------------------------------------------------------------------------ */

VPY_EXPORT int32_t vpy_buffer_clear(void) {
  g_buffer_length = 0;
  return VPY_OK;
}

VPY_EXPORT int32_t vpy_buffer_append(const char *chunk, int32_t length) {
  size_t required;

  if (length <= 0) {
    return VPY_OK;
  }
  required = g_buffer_length + (size_t)length;
  if (required > g_buffer_capacity) {
    size_t capacity = g_buffer_capacity == 0 ? 4096 : g_buffer_capacity;
    char *buffer;

    while (capacity < required) {
      capacity *= 2;
    }
    buffer = (char *)realloc(g_buffer, capacity);
    if (buffer == NULL) {
      set_error("Out of memory while transferring a string to Python");
      return VPY_ERROR;
    }
    g_buffer = buffer;
    g_buffer_capacity = capacity;
  }
  memcpy(g_buffer + g_buffer_length, chunk, (size_t)length);
  g_buffer_length = required;
  return VPY_OK;
}

/* Start a new operation: initialize on first use, reset arguments and result. */
VPY_EXPORT int32_t vpy_begin(void) {
  PyGILState_STATE gil;
  int status = VPY_OK;

  if (initialize() != VPY_OK) {
    return VPY_ERROR;
  }

  gil = PyGILState_Ensure();
  clear_result();
  clear_pending();
  Py_CLEAR(g_args);
  g_args = PyList_New(0);
  if (g_args == NULL) {
    set_error_from_python();
    status = VPY_ERROR;
  }
  PyGILState_Release(gil);
  return status;
}

VPY_EXPORT int32_t vpy_push_string(void) {
  PyGILState_STATE gil;

  REQUIRE_READY();
  gil = PyGILState_Ensure();
  int status = append_arg(buffer_as_str());
  PyGILState_Release(gil);
  return status;
}

VPY_EXPORT int32_t vpy_push_integer(int32_t value) {
  PyGILState_STATE gil;

  REQUIRE_READY();
  gil = PyGILState_Ensure();
  int status = append_arg(PyLong_FromLong((long)value));
  PyGILState_Release(gil);
  return status;
}

VPY_EXPORT int32_t vpy_push_real(double value) {
  PyGILState_STATE gil;

  REQUIRE_READY();
  gil = PyGILState_Ensure();
  int status = append_arg(PyFloat_FromDouble(value));
  PyGILState_Release(gil);
  return status;
}

VPY_EXPORT int32_t vpy_push_boolean(int32_t value) {
  PyGILState_STATE gil;

  REQUIRE_READY();
  gil = PyGILState_Ensure();
  int status = append_arg(PyBool_FromLong((long)(value != 0)));
  PyGILState_Release(gil);
  return status;
}

/* Push the buffer, holding the bits of a signed/unsigned value, as a Python int. */
VPY_EXPORT int32_t vpy_push_bits(int32_t is_signed) {
  PyGILState_STATE gil;

  REQUIRE_READY();
  gil = PyGILState_Ensure();
  PyObject *bits = buffer_as_str();
  PyObject *value = NULL;

  if (bits != NULL) {
    value = PyObject_CallMethod(g_runtime, "bits_to_int", "Oi", bits, (int)(is_signed != 0));
    Py_DECREF(bits);
  }
  {
    int status = append_arg(value);
    PyGILState_Release(gil);
    return status;
  }
}

/*
 * Push an integer_array_t argument. Allocates the Python-owned storage that
 * the element values are subsequently written to with vpy_array_write.
 */
VPY_EXPORT int32_t vpy_push_array(int32_t length, int32_t width, int32_t height, int32_t depth,
                                  int32_t bit_width, int32_t is_signed) {
  PyGILState_STATE gil;
  PyObject *storage;
  PyObject *array;
  int status;

  REQUIRE_READY();
  gil = PyGILState_Ensure();
  clear_pending();
  if (length < 0) {
    set_error("Internal error: negative array length");
    PyGILState_Release(gil);
    return VPY_ERROR;
  }

  storage = PyByteArray_FromStringAndSize(NULL, (Py_ssize_t)length * (Py_ssize_t)sizeof(int32_t));
  if (storage == NULL) {
    set_error_from_python();
    PyGILState_Release(gil);
    return VPY_ERROR;
  }
  if (length > 0) {
    memset(PyByteArray_AsString(storage), 0, (size_t)length * sizeof(int32_t));
  }

  array = PyObject_CallMethod(g_runtime, "make_array", "Oiiiiii", storage, (int)length, (int)width,
                              (int)height, (int)depth, (int)bit_width, (int)(is_signed != 0));
  if (array == NULL) {
    Py_DECREF(storage);
    set_error_from_python();
    PyGILState_Release(gil);
    return VPY_ERROR;
  }

  /* The array keeps the storage alive; we keep our own reference too so the
   * cached data pointer is valid until the next clear_pending(). NumPy holds
   * a buffer export on the bytearray, which prevents it from being resized. */
  g_pending = storage;
  g_pending_data = PyByteArray_AsString(storage);
  g_pending_size = (size_t)length * sizeof(int32_t);
  g_pending_position = 0;

  status = append_arg(array);
  PyGILState_Release(gil);
  return status;
}

VPY_EXPORT int32_t vpy_array_write(const int32_t *chunk, int32_t length) {
  size_t bytes;

  if (length <= 0) {
    return VPY_OK;
  }
  bytes = (size_t)length * sizeof(int32_t);
  if (g_pending_data == NULL || g_pending_position + bytes > g_pending_size) {
    set_error("Internal error: array data written out of bounds");
    return VPY_ERROR;
  }
  memcpy(g_pending_data + g_pending_position, chunk, bytes);
  g_pending_position += bytes;
  return VPY_OK;
}

/* Execute the buffer as Python source (is_file = 0) or as a file name (is_file = 1). */
VPY_EXPORT int32_t vpy_execute(int32_t is_file) {
  PyGILState_STATE gil;
  PyObject *text;
  PyObject *ret = NULL;
  int status = VPY_ERROR;

  REQUIRE_READY();
  flush_c_streams();
  gil = PyGILState_Ensure();
  text = buffer_as_str();
  if (text != NULL) {
    ret = PyObject_CallMethod(g_runtime, "execute", "Oi", text, (int)(is_file != 0));
    Py_DECREF(text);
  }
  if (ret == NULL) {
    set_error_from_python();
  } else {
    status = VPY_OK;
  }
  Py_XDECREF(ret);
  PyGILState_Release(gil);
  return status;
}

/* Call the function named by the buffer with the pushed arguments. */
VPY_EXPORT int32_t vpy_call(void) {
  PyGILState_STATE gil;
  PyObject *name;
  PyObject *ret = NULL;
  int status = VPY_ERROR;

  REQUIRE_READY();
  flush_c_streams();
  gil = PyGILState_Ensure();
  clear_pending();
  if (g_args == NULL) {
    set_error("Internal error: vpy_call without vpy_begin");
    PyGILState_Release(gil);
    return VPY_ERROR;
  }
  /* Note: Python data symbols (e.g. Py_None) must not be referenced since
   * the Windows DLL delay-loads the Python DLL, which only supports functions. */
  name = buffer_as_str();
  if (name != NULL) {
    ret = PyObject_CallMethod(g_runtime, "call", "OO", name, g_args);
    Py_DECREF(name);
  }
  if (ret == NULL) {
    set_error_from_python();
  } else {
    status = VPY_OK;
  }
  Py_XDECREF(ret);
  Py_CLEAR(g_args);
  PyGILState_Release(gil);
  return status;
}

/*
 * Convert the result of the last call to the requested VHDL kind. The runtime
 * returns (integer, real, bytes, meta-tuple); which fields are meaningful
 * depends on the kind.
 */
VPY_EXPORT int32_t vpy_result(int32_t kind, int32_t width) {
  PyGILState_STATE gil;
  PyObject *ret;
  PyObject *item;
  Py_ssize_t index, meta_length;
  int status = VPY_ERROR;

  REQUIRE_READY();
  gil = PyGILState_Ensure();
  clear_result();
  ret = PyObject_CallMethod(g_runtime, "convert_result", "ii", (int)kind, (int)width);
  if (ret == NULL) {
    set_error_from_python();
    goto done;
  }
  if (!PyTuple_Check(ret) || PyTuple_Size(ret) != 4) {
    set_error("Internal error: unexpected result conversion format");
    goto done;
  }

  item = PyTuple_GetItem(ret, 0); /* borrowed */
  g_result_integer = (int32_t)PyLong_AsLong(item);
  if (PyErr_Occurred()) {
    set_error_from_python();
    goto done;
  }

  item = PyTuple_GetItem(ret, 1); /* borrowed */
  g_result_real = PyFloat_AsDouble(item);
  if (PyErr_Occurred()) {
    set_error_from_python();
    goto done;
  }

  item = PyTuple_GetItem(ret, 2); /* borrowed */
  if (!PyBytes_Check(item)) {
    set_error("Internal error: result data is not bytes");
    goto done;
  }
  Py_INCREF(item);
  g_result = item;
  g_result_data = PyBytes_AsString(item);
  g_result_size = (size_t)PyBytes_Size(item);

  item = PyTuple_GetItem(ret, 3); /* borrowed */
  if (!PyTuple_Check(item)) {
    set_error("Internal error: result metadata is not a tuple");
    goto done;
  }
  meta_length = PyTuple_Size(item);
  for (index = 0; index < meta_length && index < VPY_NUM_META; index++) {
    g_result_meta[index] = (int32_t)PyLong_AsLong(PyTuple_GetItem(item, index));
    if (PyErr_Occurred()) {
      set_error_from_python();
      goto done;
    }
  }
  status = VPY_OK;

done:
  if (status != VPY_OK) {
    clear_result();
  }
  Py_XDECREF(ret);
  PyGILState_Release(gil);
  return status;
}

VPY_EXPORT int32_t vpy_result_integer(void) { return g_result_integer; }

VPY_EXPORT double vpy_result_real(void) { return g_result_real; }

VPY_EXPORT int32_t vpy_result_meta(int32_t index) {
  if (index < 0 || index >= VPY_NUM_META) {
    return 0;
  }
  return g_result_meta[index];
}

/* Copy length bytes of the string/bit-string result starting at offset. */
VPY_EXPORT void vpy_result_read_string(char *chunk, int32_t offset, int32_t length) {
  if (offset < 0 || length <= 0 || (size_t)offset + (size_t)length > g_result_size) {
    return;
  }
  memcpy(chunk, g_result_data + offset, (size_t)length);
}

/* Copy length integers of the array result starting at element offset. */
VPY_EXPORT void vpy_result_read_integers(int32_t *chunk, int32_t offset, int32_t length) {
  size_t start = (size_t)offset * sizeof(int32_t);
  size_t bytes = (size_t)length * sizeof(int32_t);

  if (offset < 0 || length <= 0 || start + bytes > g_result_size) {
    return;
  }
  memcpy(chunk, g_result_data + start, bytes);
}

VPY_EXPORT int32_t vpy_error_length(void) {
  return g_error_length > INT32_MAX ? INT32_MAX : (int32_t)g_error_length;
}

VPY_EXPORT void vpy_error_read(char *chunk, int32_t offset, int32_t length) {
  if (g_error == NULL || offset < 0 || length <= 0 || (size_t)offset + (size_t)length > g_error_length) {
    return;
  }
  memcpy(chunk, g_error + offset, (size_t)length);
}
