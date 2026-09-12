// This package provides a dictionary types and operations
//
// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this file,
// You can obtain one at http://mozilla.org/MPL/2.0/.
//
// Copyright (c) 2014-2023, Lars Asplund lars.anders.asplund@gmail.com

#ifndef _WIN32
#ifndef _GNU_SOURCE
#define _GNU_SOURCE  // dladdr
#endif
#endif

#include <stdbool.h>
#include <stdio.h>

#include "mti.h"

#ifndef _WIN32
#include <dlfcn.h>
#endif
#include "python_pkg.h"

#define MAX_VHDL_PARAMETER_STRING_LENGTH 100000

PyObject* globals = NULL;
PyObject* locals = NULL;

void python_cleanup(void);

static void py_error_handler(const char* context, const char* code_or_expr,
                             const char* reason, bool cleanup) {
  const char* unknown_error = "Unknown error";

  // Use provided error reason or try extracting the reason from the Python
  // exception
  if (reason == NULL) {
    PyObject *ptype, *pvalue, *ptraceback;
    PyErr_Fetch(&ptype, &pvalue, &ptraceback);
    if (ptype != NULL) {
      reason = get_string(pvalue);
    }
    // The reason has been extracted. The exception must not be left set:
    // Py_FinalizeEx in python_cleanup would fail on it.
    Py_XDECREF(ptype);
    Py_XDECREF(pvalue);
    Py_XDECREF(ptraceback);
  }

  // Clean-up Python session first in case vhpi_assert stops the simulation
  if (cleanup) {
    python_cleanup();
  }

  // Output error message
  reason = reason == NULL ? unknown_error : reason;
  if (code_or_expr == NULL) {
    printf("ERROR %s:\n\n%s\n\n", context, reason);
  } else {
    printf("ERROR %s:\n\n%s\n\n%s\n\n", context, code_or_expr, reason);
  }
  mti_FatalError();
}

static void ffi_error_handler(const char* context, bool cleanup) {
  // Clean-up Python session first in case vhpi_assert stops the simulation
  if (cleanup) {
    python_cleanup();
  }

  printf("ERROR %s\n\n", context);
  mti_FatalError();
}

#ifndef _WIN32
// The simulator loads this application with RTLD_LOCAL, which hides the
// Python symbols from extension modules such as _ctypes and NumPy that expect
// them to be global. Re-open the Python library, already loaded as a
// dependency of this application, with RTLD_GLOBAL.
static void make_python_library_global(void) {
  Dl_info info;

  if (dladdr((void*)&Py_Initialize, &info) != 0 && info.dli_fname != NULL) {
    dlopen(info.dli_fname, RTLD_NOW | RTLD_GLOBAL | RTLD_NOLOAD);
  }
}
#endif

void python_setup(void) {
#ifndef _WIN32
  make_python_library_global();
#endif
  Py_Initialize();
  if (!Py_IsInitialized()) {
    ffi_error_handler("Failed to initialize Python", false);
  }

  PyObject* main_module = PyImport_AddModule("__main__");
  if (main_module == NULL) {
    ffi_error_handler("Failed to get the main module", true);
  }

  globals = PyModule_GetDict(main_module);
  if (globals == NULL) {
    ffi_error_handler("Failed to get the global dictionary", true);
  }

  // globals and locals are the same at the top-level
  locals = globals;

  register_py_error_handler(py_error_handler);
  register_ffi_error_handler(ffi_error_handler);

  // This class allow us to evaluate an expression and get the length of the
  // result before getting the result. The length is used to allocate a VHDL
  // array before getting the result. This saves us from passing and evaluating
  // the expression twice (both when getting its length and its value). When
  // only supporting Python 3.8+, this can be solved with the walrus operator:
  // len(__eval_result__ := expr)
  char* code =
      "\
class __EvalResult__():\n\
    def __init__(self):\n\
        self._result = None\n\
    def set(self, expr):\n\
        self._result = expr\n\
        return len(self._result)\n\
    def get(self):\n\
        return self._result\n\
__eval_result__=__EvalResult__()\n";

  if (PyRun_String(code, Py_file_input, globals, locals) == NULL) {
    ffi_error_handler("Failed to initialize predefined Python objects", true);
  }
}

void python_cleanup(void) {
  if (locals != NULL) {
    Py_DECREF(locals);
  }

  if (Py_FinalizeEx()) {
    printf("WARNING: Failed to finalize Python\n");
  }
}

static const char* get_parameter(mtiVariableIdT id) {
  mtiTypeIdT type;
  int len;
  static char vhdl_parameter_string[MAX_VHDL_PARAMETER_STRING_LENGTH];

  type = mti_GetVarType(id);
  len = mti_TickLength(type);
  mti_GetArrayVarValue(id, vhdl_parameter_string);
  vhdl_parameter_string[len] = 0;

  return vhdl_parameter_string;
}

int eval_integer(mtiVariableIdT id) {
  const char* expr = get_parameter(id);

  // Eval(uate) expression in Python
  PyObject* eval_result = eval(expr);

  // Return result to VHDL
  return get_integer(eval_result, expr, true);
}

mtiRealT eval_real(mtiVariableIdT id) {
  const char* expr = get_parameter(id);
  mtiRealT result;

  // Eval(uate) expression in Python
  PyObject* eval_result = eval(expr);

  // Return result to VHDL
  MTI_ASSIGN_TO_REAL(result, get_real(eval_result, expr, true));
  return result;
}

void p_get_integer_vector(mtiVariableIdT vec) {
  // Get evaluation result from Python
  PyObject* eval_result = eval("__eval_result__.get()");

  // Check that the eval results in a list. TODO: tuple and sets of integers
  // should also work
  if (!PyList_Check(eval_result)) {
    handle_type_check_error(eval_result, "evaluating to integer_vector",
                            "__eval_result__.get()");
  }

  const int list_size = PyList_GET_SIZE(eval_result);
  const int vec_len = mti_TickLength(mti_GetVarType(vec));
  int* arr = (int*)mti_GetArrayVarValue(vec, NULL);

  for (int idx = 0; idx < vec_len; idx++) {
    arr[idx] = get_integer(PyList_GetItem(eval_result, idx),
                           "__eval_result__.get()", false);
  }
  Py_DECREF(eval_result);
}

void p_get_real_vector(mtiVariableIdT vec) {
  // Get evaluation result from Python
  PyObject* eval_result = eval("__eval_result__.get()");

  // Check that the eval results in a list. TODO: tuple and sets of integers
  // should also work
  if (!PyList_Check(eval_result)) {
    handle_type_check_error(eval_result, "evaluating to real_vector",
                            "__eval_result__.get()");
  }

  const int list_size = PyList_GET_SIZE(eval_result);
  const int vec_len = mti_TickLength(mti_GetVarType(vec));
  double* arr = (double*)mti_GetArrayVarValue(vec, NULL);

  for (int idx = 0; idx < vec_len; idx++) {
    arr[idx] = get_real(PyList_GetItem(eval_result, idx),
                        "__eval_result__.get()", false);
  }
  Py_DECREF(eval_result);
}

void p_get_string(mtiVariableIdT vec) {
  // Get evaluation result from Python
  PyObject* eval_result = eval("__eval_result__.get()");

  const char* py_str = get_string(eval_result);
  char* vhdl_str = (char*)mti_GetArrayVarValue(vec, NULL);
  strcpy(vhdl_str, py_str);

  Py_DECREF(eval_result);
}

void exec(mtiVariableIdT id) {
  // Get code parameter from VHDL procedure call
  const char* code = get_parameter(id);

  // Exec(ute) Python code
  if (PyRun_String(code, Py_file_input, globals, locals) == NULL) {
    py_error_handler("executing", code, NULL, true);
  }
}
