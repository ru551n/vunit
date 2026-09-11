/*
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this file,
 * You can obtain one at http://mozilla.org/MPL/2.0/.
 *
 * Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com
 *
 * The operations: begin, execute, call, and the conversion and transfer of
 * the call result back to VHDL.
 */

#include "bridge.h"

#include <stdio.h>
#include <string.h>

#define NUM_META 8

/* Result of the last call, converted to a VHDL kind by vpy_result. */
static PyObject *g_result = NULL; /* bytes of a string/array result, strong reference */
static const char *g_result_data = NULL; /* view into g_result, immutable */
static size_t g_result_size = 0;
static int32_t g_result_integer = 0;
static double g_result_real = 0.0;
static int32_t g_result_meta[NUM_META];

/* Drop the result of the previous call. GIL held. */
static void clear_result(void) {
  Py_CLEAR(g_result);
  g_result_data = NULL;
  g_result_size = 0;
  g_result_integer = 0;
  g_result_real = 0.0;
  memset(g_result_meta, 0, sizeof(g_result_meta));
}

/* Keep the simulator's and Python's output in order. */
static void flush_c_streams(void) {
  fflush(stdout);
  fflush(stderr);
}

/*
 * Start a new operation in the session named by the buffer: initialize on
 * first use, select the session and reset arguments and result.
 */
VPY_EXPORT int32_t vpy_begin(void) {
  PyGILState_STATE gil;
  PyObject *session;
  int status = VPY_ERROR;

  if (vpy_initialize() != VPY_OK || vpy_enter(&gil) != VPY_OK) {
    return VPY_ERROR;
  }
  clear_result();
  vpy_clear_arguments();
  session = vpy_buffer_as_str();
  if (session == NULL) {
    vpy_set_error_from_python();
  } else {
    status = vpy_finish_call(PyObject_CallMethod(vpy_runtime(), "select_session", "O", session));
    Py_DECREF(session);
  }
  if (status == VPY_OK) {
    status = vpy_reset_arguments();
  }
  PyGILState_Release(gil);
  return status;
}

/* Execute the buffer as Python source (is_file = 0) or as a file name (is_file = 1). */
VPY_EXPORT int32_t vpy_execute(int32_t is_file) {
  PyGILState_STATE gil;
  PyObject *text;
  int status = VPY_ERROR;

  flush_c_streams();
  if (vpy_enter(&gil) != VPY_OK) {
    return VPY_ERROR;
  }
  text = vpy_buffer_as_str();
  if (text == NULL) {
    vpy_set_error_from_python();
  } else {
    status = vpy_finish_call(PyObject_CallMethod(vpy_runtime(), "execute", "Oi", text, (int)(is_file != 0)));
    Py_DECREF(text);
  }
  PyGILState_Release(gil);
  return status;
}

/* Call the function named by the buffer with the pushed arguments. */
VPY_EXPORT int32_t vpy_call(void) {
  PyGILState_STATE gil;
  PyObject *name;
  int status = VPY_ERROR;

  flush_c_streams();
  if (vpy_enter(&gil) != VPY_OK) {
    return VPY_ERROR;
  }
  if (vpy_arguments() == NULL) {
    vpy_set_error("Internal error: vpy_call without vpy_begin");
  } else {
    name = vpy_buffer_as_str();
    if (name == NULL) {
      vpy_set_error_from_python();
    } else {
      status = vpy_finish_call(PyObject_CallMethod(vpy_runtime(), "call", "OO", name, vpy_arguments()));
      Py_DECREF(name);
    }
  }
  vpy_clear_arguments();
  PyGILState_Release(gil);
  return status;
}

/*
 * Store the conversion result (integer, real, bytes, meta tuple) returned by
 * the runtime. Which fields are meaningful depends on the kind. GIL held.
 */
static int store_result(PyObject *converted) {
  PyObject *data;
  PyObject *meta;
  Py_ssize_t index;

  if (!PyTuple_Check(converted) || PyTuple_Size(converted) != 4) {
    vpy_set_error("Internal error: unexpected result conversion format");
    return VPY_ERROR;
  }
  data = PyTuple_GetItem(converted, 2); /* borrowed */
  meta = PyTuple_GetItem(converted, 3); /* borrowed */
  if (!PyBytes_Check(data) || !PyTuple_Check(meta)) {
    vpy_set_error("Internal error: unexpected result conversion format");
    return VPY_ERROR;
  }

  g_result_integer = (int32_t)PyLong_AsLong(PyTuple_GetItem(converted, 0));
  g_result_real = PyFloat_AsDouble(PyTuple_GetItem(converted, 1));
  for (index = 0; index < PyTuple_Size(meta) && index < NUM_META; index++) {
    g_result_meta[index] = (int32_t)PyLong_AsLong(PyTuple_GetItem(meta, index));
  }
  if (PyErr_Occurred()) {
    vpy_set_error_from_python();
    return VPY_ERROR;
  }

  Py_INCREF(data);
  g_result = data;
  g_result_data = PyBytes_AsString(data);
  g_result_size = (size_t)PyBytes_Size(data);
  return VPY_OK;
}

/* Convert the result of the last call to the requested VHDL kind and width. */
VPY_EXPORT int32_t vpy_result(int32_t kind, int32_t width) {
  PyGILState_STATE gil;
  PyObject *converted;
  int status = VPY_ERROR;

  if (vpy_enter(&gil) != VPY_OK) {
    return VPY_ERROR;
  }
  clear_result();
  converted = PyObject_CallMethod(vpy_runtime(), "convert_result", "ii", (int)kind, (int)width);
  if (converted == NULL) {
    vpy_set_error_from_python();
  } else {
    status = store_result(converted);
    Py_DECREF(converted);
  }
  if (status != VPY_OK) {
    clear_result();
  }
  PyGILState_Release(gil);
  return status;
}

VPY_EXPORT int32_t vpy_result_integer(void) { return g_result_integer; }

VPY_EXPORT double vpy_result_real(void) { return g_result_real; }

VPY_EXPORT int32_t vpy_result_meta(int32_t index) {
  if (index < 0 || index >= NUM_META) {
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
