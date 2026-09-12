-- This package provides a dictionary types and operations
--
-- This Source Code Form is subject to the terms of the Mozilla Public
-- License, v. 2.0. If a copy of the MPL was not distributed with this file,
-- You can obtain one at http://mozilla.org/MPL/2.0/.
--
-- Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

use std.textio.all;

package python_ffi_pkg is
  -- A session is a named Python namespace. Only the default session, the
  -- __main__ namespace, is supported by this foreign language interface.
  type python_session_t is array (positive range <>) of character;
  constant default_session : python_session_t := "default";

  procedure python_setup;
  -- TODO: Looks like Riviera-PRO requires the path to the shared library to be fixed at compile time
  -- and that may become a bit limited. VHDL standard allow for expressions.
  attribute foreign of python_setup : procedure is "VHPI libraries/python python_setup";
  procedure python_cleanup;
  attribute foreign of python_cleanup : procedure is "VHPI libraries/python python_cleanup";

  function p_eval_integer(expr : string) return integer;
  attribute foreign of p_eval_integer : function is "VHPI libraries/python eval_integer";
  function eval_integer(expr : string; session : python_session_t := default_session) return integer;
  alias eval is eval_integer[string, python_session_t return integer];

  function p_eval_real(expr : string) return real;
  attribute foreign of p_eval_real : function is "VHPI libraries/python eval_real";
  function eval_real(expr : string; session : python_session_t := default_session) return real;
  alias eval is eval_real[string, python_session_t return real];

  function p_eval_integer_vector(expr : string) return integer_vector;
  attribute foreign of p_eval_integer_vector : function is "VHPI libraries/python eval_integer_vector";
  function eval_integer_vector(
    expr : string; session : python_session_t := default_session
  ) return integer_vector;
  alias eval is eval_integer_vector[string, python_session_t return integer_vector];

  function p_eval_real_vector(expr : string) return real_vector;
  attribute foreign of p_eval_real_vector : function is "VHPI libraries/python eval_real_vector";
  function eval_real_vector(
    expr : string; session : python_session_t := default_session
  ) return real_vector;
  alias eval is eval_real_vector[string, python_session_t return real_vector];

  function p_eval_string(expr : string) return string;
  attribute foreign of p_eval_string : function is "VHPI libraries/python eval_string";
  function eval_string(expr : string; session : python_session_t := default_session) return string;
  alias eval is eval_string[string, python_session_t return string];

  procedure p_exec(code : string);
  attribute foreign of p_exec : procedure is "VHPI libraries/python exec";
  procedure exec(code : string; session : python_session_t := default_session);
end package;

package body python_ffi_pkg is
  procedure p_check_session(session : python_session_t) is
  begin
    if session /= default_session then
      report "Python sessions are only supported with NVC and GHDL" severity failure;
    end if;
  end;

  function eval_integer(expr : string; session : python_session_t := default_session) return integer is
  begin
    p_check_session(session);
    return p_eval_integer(expr);
  end;

  function eval_real(expr : string; session : python_session_t := default_session) return real is
  begin
    p_check_session(session);
    return p_eval_real(expr);
  end;

  function eval_integer_vector(
    expr : string; session : python_session_t := default_session
  ) return integer_vector is
  begin
    p_check_session(session);
    return p_eval_integer_vector(expr);
  end;

  function eval_real_vector(
    expr : string; session : python_session_t := default_session
  ) return real_vector is
  begin
    p_check_session(session);
    return p_eval_real_vector(expr);
  end;

  function eval_string(expr : string; session : python_session_t := default_session) return string is
  begin
    p_check_session(session);
    return p_eval_string(expr);
  end;

  procedure exec(code : string; session : python_session_t := default_session) is
  begin
    p_check_session(session);
    p_exec(code);
  end;
end package body;
