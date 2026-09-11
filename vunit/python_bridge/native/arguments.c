/*
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this file,
 * You can obtain one at http://mozilla.org/MPL/2.0/.
 *
 * Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com
 *
 * Data transferred from VHDL: a byte buffer for strings (source code, file
 * and function names, string arguments) and the positional arguments of the
 * next python_call.
 */

#include "bridge.h"

#include <stdlib.h>
#include <string.h>

/* Raw byte buffer used to transfer strings from VHDL, owned. */
static char *g_buffer = NULL;
static size_t g_buffer_length = 0;
static size_t g_buffer_capacity = 0;

/* Strong references, only touched with the GIL held. */
static PyObject *g_args = NULL;    /* list of positional arguments of the next call */
static PyObject *g_pending = NULL; /* bytearray backing the array argument being filled */

/* View into g_pending. It is owned by us and never resized while referenced
 * (NumPy holds a buffer export on it), so the pointer stays valid. */
static char *g_pending_data = NULL;
static size_t g_pending_size = 0;
static size_t g_pending_position = 0;

/* New reference to the transfer buffer as a str. */
PyObject *vpy_buffer_as_str(void) {
  return PyUnicode_DecodeUTF8(g_buffer == NULL ? "" : g_buffer, (Py_ssize_t)g_buffer_length, "surrogateescape");
}

static void clear_pending(void) {
  Py_CLEAR(g_pending);
  g_pending_data = NULL;
  g_pending_size = 0;
  g_pending_position = 0;
}

void vpy_clear_arguments(void) {
  clear_pending();
  Py_CLEAR(g_args);
}

int vpy_reset_arguments(void) {
  vpy_clear_arguments();
  g_args = PyList_New(0);
  if (g_args == NULL) {
    vpy_set_error_from_python();
    return VPY_ERROR;
  }
  return VPY_OK;
}

PyObject *vpy_arguments(void) { return g_args; }

/* Append value (a new reference or NULL, which is consumed) to the arguments. */
static int append_argument(PyObject *value) {
  int status;

  if (value == NULL) {
    vpy_set_error_from_python();
    return VPY_ERROR;
  }
  if (g_args == NULL) {
    Py_DECREF(value);
    vpy_set_error("Internal error: argument pushed before vpy_begin");
    return VPY_ERROR;
  }
  status = PyList_Append(g_args, value);
  Py_DECREF(value);
  if (status < 0) {
    vpy_set_error_from_python();
    return VPY_ERROR;
  }
  return VPY_OK;
}

/* ------------------------------------------------------------------------ */
/* Entry points                                                              */
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
      vpy_set_error("Out of memory while transferring a string to Python");
      return VPY_ERROR;
    }
    g_buffer = buffer;
    g_buffer_capacity = capacity;
  }
  memcpy(g_buffer + g_buffer_length, chunk, (size_t)length);
  g_buffer_length = required;
  return VPY_OK;
}

VPY_EXPORT int32_t vpy_push_string(void) {
  PyGILState_STATE gil;
  int status;

  if (vpy_enter(&gil) != VPY_OK) {
    return VPY_ERROR;
  }
  status = append_argument(vpy_buffer_as_str());
  PyGILState_Release(gil);
  return status;
}

VPY_EXPORT int32_t vpy_push_integer(int32_t value) {
  PyGILState_STATE gil;
  int status;

  if (vpy_enter(&gil) != VPY_OK) {
    return VPY_ERROR;
  }
  status = append_argument(PyLong_FromLong((long)value));
  PyGILState_Release(gil);
  return status;
}

VPY_EXPORT int32_t vpy_push_real(double value) {
  PyGILState_STATE gil;
  int status;

  if (vpy_enter(&gil) != VPY_OK) {
    return VPY_ERROR;
  }
  status = append_argument(PyFloat_FromDouble(value));
  PyGILState_Release(gil);
  return status;
}

VPY_EXPORT int32_t vpy_push_boolean(int32_t value) {
  PyGILState_STATE gil;
  int status;

  if (vpy_enter(&gil) != VPY_OK) {
    return VPY_ERROR;
  }
  status = append_argument(PyBool_FromLong((long)(value != 0)));
  PyGILState_Release(gil);
  return status;
}

/* Push the buffer, holding the bits of a signed/unsigned value (MSB first), as a Python int. */
VPY_EXPORT int32_t vpy_push_bits(int32_t is_signed) {
  PyGILState_STATE gil;
  PyObject *bits;
  PyObject *value = NULL;
  int status;

  if (vpy_enter(&gil) != VPY_OK) {
    return VPY_ERROR;
  }
  bits = vpy_buffer_as_str();
  if (bits != NULL) {
    value = PyObject_CallMethod(vpy_runtime(), "bits_to_int", "Oi", bits, (int)(is_signed != 0));
    Py_DECREF(bits);
  }
  status = append_argument(value);
  PyGILState_Release(gil);
  return status;
}

/*
 * Push an integer_array_t argument. Allocates the Python-owned storage that
 * the element values are subsequently written to with vpy_array_write.
 */
VPY_EXPORT int32_t vpy_push_array(int32_t length, int32_t width, int32_t height, int32_t depth, int32_t bit_width,
                                  int32_t is_signed) {
  PyGILState_STATE gil;
  size_t size = length > 0 ? (size_t)length * sizeof(int32_t) : 0;
  PyObject *storage = NULL;
  PyObject *array = NULL;
  int status = VPY_ERROR;

  if (vpy_enter(&gil) != VPY_OK) {
    return VPY_ERROR;
  }
  clear_pending();
  if (length < 0) {
    vpy_set_error("Internal error: negative array length");
    goto done;
  }

  storage = PyByteArray_FromStringAndSize(NULL, (Py_ssize_t)size);
  if (storage == NULL) {
    vpy_set_error_from_python();
    goto done;
  }
  if (size > 0) {
    memset(PyByteArray_AsString(storage), 0, size);
  }

  array = PyObject_CallMethod(vpy_runtime(), "make_array", "Oiiiiii", storage, (int)length, (int)width, (int)height,
                              (int)depth, (int)bit_width, (int)(is_signed != 0));
  if (array == NULL) {
    vpy_set_error_from_python();
    goto done;
  }

  /* The array keeps the storage alive; we keep our own reference too so the
   * cached data pointer is valid until the next clear_pending(). */
  g_pending = storage;
  storage = NULL;
  g_pending_data = PyByteArray_AsString(g_pending);
  g_pending_size = size;
  g_pending_position = 0;
  status = append_argument(array);

done:
  Py_XDECREF(storage);
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
    vpy_set_error("Internal error: array data written out of bounds");
    return VPY_ERROR;
  }
  memcpy(g_pending_data + g_pending_position, chunk, bytes);
  g_pending_position += bytes;
  return VPY_OK;
}
