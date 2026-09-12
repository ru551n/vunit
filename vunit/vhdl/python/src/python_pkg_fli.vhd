-- This package provides a dictionary types and operations
--
-- This Source Code Form is subject to the terms of the Mozilla Public
-- License, v. 2.0. If a copy of the MPL was not distributed with this file,
-- You can obtain one at http://mozilla.org/MPL/2.0/.
--
-- Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

use std.textio.all;

use work.integer_array_pkg.all;
use work.logger_pkg.all;

package python_ffi_pkg is
  -- A session is a named Python namespace. Only the default session, the
  -- __main__ namespace, is supported by this foreign language interface.
  type python_session_t is array (positive range <>) of character;
  constant default_session : python_session_t := "default";

  procedure python_setup;
  attribute foreign of python_setup : procedure is "python_setup libraries/python/python_fli.so";
  procedure python_cleanup;
  attribute foreign of python_cleanup : procedure is "python_cleanup libraries/python/python_fli.so";

  function p_eval_integer(expr : string) return integer;
  attribute foreign of p_eval_integer : function is "eval_integer libraries/python/python_fli.so";
  function eval_integer(expr : string; session : python_session_t := default_session) return integer;
  alias eval is eval_integer[string, python_session_t return integer];

  function p_eval_real(expr : string) return real;
  attribute foreign of p_eval_real : function is "eval_real libraries/python/python_fli.so";
  function eval_real(expr : string; session : python_session_t := default_session) return real;
  alias eval is eval_real[string, python_session_t return real];

  function eval_integer_vector(
    expr : string; session : python_session_t := default_session
  ) return integer_vector;
  alias eval is eval_integer_vector[string, python_session_t return integer_vector];

  procedure p_get_integer_vector(vec : out integer_vector);
  attribute foreign of p_get_integer_vector : procedure is "p_get_integer_vector libraries/python/python_fli.so";

  function eval_real_vector(
    expr : string; session : python_session_t := default_session
  ) return real_vector;
  alias eval is eval_real_vector[string, python_session_t return real_vector];

  procedure p_get_real_vector(vec : out real_vector);
  attribute foreign of p_get_real_vector : procedure is "p_get_real_vector libraries/python/python_fli.so";

  function eval_string(expr : string; session : python_session_t := default_session) return string;
  alias eval is eval_string[string, python_session_t return string];

  procedure p_get_string(vec : out string);
  attribute foreign of p_get_string : procedure is "p_get_string libraries/python/python_fli.so";

  procedure p_exec(code : string);
  attribute foreign of p_exec : procedure is "exec libraries/python/python_fli.so";
  procedure exec(code : string; session : python_session_t := default_session);

  -----------------------------------------------------------------------------
  -- Private, the primitives the bridge operations of python_pkg are built on
  -----------------------------------------------------------------------------
  -- Some operations of python_pkg are implemented by the VUnit Python
  -- bridge, which is only available for NVC and GHDL. The primitives below
  -- are declared so that python_pkg has one body for every simulator, but
  -- they report a failure when they are used.

  -- Logger used to report Python errors
  constant python_logger : logger_t := get_logger("vunit_lib:python");

  -- Result kinds, must match vunit/python_bridge/runtime.py
  constant p_kind_integer : integer := 0;
  constant p_kind_real : integer := 1;
  constant p_kind_boolean : integer := 2;
  constant p_kind_string : integer := 3;
  constant p_kind_std_ulogic : integer := 4;
  constant p_kind_std_ulogic_vector : integer := 5;
  constant p_kind_signed : integer := 6;
  constant p_kind_unsigned : integer := 7;
  constant p_kind_integer_array : integer := 8;
  constant p_kind_integer_vector : integer := 9;
  constant p_kind_real_vector : integer := 10;

  -- Name of the operation, used in the error messages
  function p_eval_operation(expr : string; session : python_session_t := default_session) return string;

  -- Execute the Python file with the given name
  impure function p_exec_file(
    file_name : string; session : python_session_t := default_session
  ) return boolean;

  -- Evaluate expr and convert the value to the VHDL type given by kind. width
  -- is the length of a std_ulogic_vector, signed or unsigned result, -1 when
  -- it is not known.
  impure function p_eval(
    expr      : string;
    kind      : integer;
    width     : integer;
    operation : string;
    session   : python_session_t := default_session
  ) return boolean;

  -- The result of the last evaluation
  impure function p_result_integer return integer;
  impure function p_result_string return string;
  impure function p_result_integer_array return integer_array_t;

  -- Transfer an integer_array_t to Python and return the id it is staged
  -- under, -1 on failure.
  impure function p_stage_array(arr : integer_array_t; operation : string) return integer;
end package;

package body python_ffi_pkg is
  procedure p_check_session(session : python_session_t) is
  begin
    if session /= default_session then
      report "Python sessions are only supported with NVC and GHDL" severity failure;
    end if;
  end;

  procedure python_setup is
  begin
    report "ERROR: Failed to call foreign subprogram" severity failure;
  end;

  procedure python_cleanup is
  begin
    report "ERROR: Failed to call foreign subprogram" severity failure;
  end;

  function p_eval_integer(expr : string) return integer is
  begin
    report "ERROR: Failed to call foreign subprogram" severity failure;
    return -1;
  end;

  function eval_integer(expr : string; session : python_session_t := default_session) return integer is
  begin
    p_check_session(session);
    return p_eval_integer(expr);
  end;

  function p_eval_real(expr : string) return real is
  begin
    report "ERROR: Failed to call foreign subprogram" severity failure;
    return -1.0;
  end;

  function eval_real(expr : string; session : python_session_t := default_session) return real is
  begin
    p_check_session(session);
    return p_eval_real(expr);
  end;

  procedure p_exec(code : string) is
  begin
    report "ERROR: Failed to call foreign subprogram" severity failure;
  end;

  procedure exec(code : string; session : python_session_t := default_session) is
  begin
    p_check_session(session);
    p_exec(code);
  end;

  function eval_integer_vector(
    expr : string; session : python_session_t := default_session
  ) return integer_vector is
    constant result_length : natural := eval_integer("__eval_result__.set(" & expr & ")", session);
    variable result : integer_vector(0 to result_length - 1);
  begin
    p_get_integer_vector(result);

    return result;
  end;

  procedure p_get_integer_vector(vec : out integer_vector) is
  begin
    report "ERROR: Failed to call foreign subprogram" severity failure;
  end;

  function eval_real_vector(
    expr : string; session : python_session_t := default_session
  ) return real_vector is
    constant result_length : natural := eval_integer("__eval_result__.set(" & expr & ")", session);
    variable result : real_vector(0 to result_length - 1);
  begin
    p_get_real_vector(result);

    return result;
  end;

  procedure p_get_real_vector(vec : out real_vector) is
  begin
    report "ERROR: Failed to call foreign subprogram" severity failure;
  end;

  function eval_string(expr : string; session : python_session_t := default_session) return string is
    constant result_length : natural := eval_integer("__eval_result__.set(" & expr & ")", session);
    -- Add one character for the C null termination such that strcpy can be used. Do not return this
    -- character
    variable result : string(1 to result_length + 1);
  begin
    p_get_string(result);

    return result(1 to result_length);
  end;

  procedure p_get_string(vec : out string) is
  begin
    report "ERROR: Failed to call foreign subprogram" severity failure;
  end;

  -----------------------------------------------------------------------------
  -- Private, the primitives the bridge operations of python_pkg are built on
  -----------------------------------------------------------------------------
  procedure p_unsupported(name : string) is
  begin
    failure(python_logger, name & " requires NVC or GHDL");
  end;

  function p_eval_operation(expr : string; session : python_session_t := default_session) return string is
  begin
    if session = default_session then
      return "eval(""" & expr & """)";
    end if;
    return "eval(""" & expr & """, session => """ & string(session) & """)";
  end;

  impure function p_exec_file(
    file_name : string; session : python_session_t := default_session
  ) return boolean is
  begin
    p_unsupported("exec_file");
    return false;
  end;

  impure function p_eval(
    expr      : string;
    kind      : integer;
    width     : integer;
    operation : string;
    session   : python_session_t := default_session
  ) return boolean is
  begin
    p_unsupported(operation);
    return false;
  end;

  impure function p_result_integer return integer is
  begin
    p_unsupported("p_result_integer");
    return integer'low;
  end;

  impure function p_result_string return string is
  begin
    p_unsupported("p_result_string");
    return "";
  end;

  impure function p_result_integer_array return integer_array_t is
  begin
    p_unsupported("p_result_integer_array");
    return null_integer_array;
  end;

  impure function p_stage_array(arr : integer_array_t; operation : string) return integer is
  begin
    p_unsupported(operation);
    return -1;
  end;
end package body;
