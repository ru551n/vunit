-- This Source Code Form is subject to the terms of the Mozilla Public
-- License, v. 2.0. If a copy of the MPL was not distributed with this file,
-- You can obtain one at http://mozilla.org/MPL/2.0/.
--
-- Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

library vunit_lib;
context vunit_lib.vunit_context;

entity tb_python_sessions is
  generic (runner_cfg : string);
end entity;

architecture tb of tb_python_sessions is
begin
  main : process
    constant golden : python_session_t := "golden";
    constant fixed_point : python_session_t := "fixed_point";
    variable arr, result : integer_array_t;
    variable value : integer;
  begin
    test_runner_setup(runner, runner_cfg);

    while test_suite loop
      if run("Test that sessions have separate namespaces") then
        python_execute("x = 1" & LF & "def get_x(): return x", session => golden);
        python_execute("x = 2" & LF & "def get_x(): return x", session => fixed_point);
        python_execute("x = 3" & LF & "def get_x(): return x");
        check_equal(integer'(python_call("get_x", session => golden)), 1);
        check_equal(integer'(python_call("get_x", session => fixed_point)), 2);
        check_equal(integer'(python_call("get_x")), 3);
        check_equal(integer'(python_call("get_x", session => default_session)), 3);

      elsif run("Test positional session argument") then
        python_execute("def get_answer(): return 42", "", golden);
        check_equal(integer'(python_call("get_answer", golden)), 42);
        python_execute("def plus_one(x): return x + 1", session => golden);
        check_equal(integer'(python_call("plus_one", 41, golden)), 42);

      elsif run("Test that a name defined in another session is not visible") then
        python_execute("def only_in_golden(): return 1", session => golden);
        mock(python_logger, failure);
        value := python_call("only_in_golden", session => fixed_point);
        check_only_log(
          python_logger,
          "python_call(""only_in_golden"", session => ""fixed_point"") failed:" & LF &
          "NameError: Python function 'only_in_golden' is not defined. Define it with python_execute first.",
          failure
        );
        unmock(python_logger);

      elsif run("Test executing a file in different sessions") then
        python_execute(file_name => "test/models/counter.py", session => golden);
        python_execute(file_name => "test/models/counter.py", session => golden);
        python_execute(file_name => "test/models/counter.py", session => fixed_point);
        check_equal(integer'(python_call("get_call_count", session => golden)), 2);
        check_equal(integer'(python_call("get_call_count", session => fixed_point)), 1);

      elsif run("Test arrays and procedure results in a session") then
        python_execute("def double(a): return a * 2", session => golden);
        arr := new_1d(3);
        for idx in 0 to 2 loop
          set(arr, idx, idx + 1);
        end loop;
        result := python_call("double", arr, session => golden);
        check_equal(get(result, 2), 6);

      elsif run("Test that imported modules are shared between sessions") then
        python_execute("import math" & LF & "math.vunit_marker = 7", session => golden);
        python_execute("import math" & LF & "def get_marker(): return math.vunit_marker", session => fixed_point);
        check_equal(integer'(python_call("get_marker", session => fixed_point)), 7);

      elsif run("Test error context names the session") then
        mock(python_logger, failure);
        python_execute("raise ValueError(""boom"")", session => golden);
        check_only_log(
          python_logger,
          "python_execute(session => ""golden"") failed:" & LF &
          "Traceback (most recent call last):" & LF &
          "  File ""<python_execute golden #1>"", line 1, in <module>" & LF &
          "    raise ValueError(""boom"")" & LF &
          "ValueError: boom",
          failure
        );
        unmock(python_logger);
      end if;
    end loop;

    test_runner_cleanup(runner);
  end process;
end architecture;
