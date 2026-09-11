-- This Source Code Form is subject to the terms of the Mozilla Public
-- License, v. 2.0. If a copy of the MPL was not distributed with this file,
-- You can obtain one at http://mozilla.org/MPL/2.0/.
--
-- Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

library vunit_lib;
context vunit_lib.vunit_context;
use vunit_lib.python_pkg.all;

entity tb_python_execute is
  generic (runner_cfg : string);
end entity;

architecture test_fixture of tb_python_execute is
begin
  main : process
  begin
    test_runner_setup(runner, runner_cfg);

    while test_suite loop

      if run("inline source") then
        python_execute(source => "X = 6 * 7");
        python_execute(source => "def get_x():" & LF & "    return X");
        check_equal(integer'(python_call("get_x")), 42);

      elsif run("multiline source with blank lines and indentation") then
        python_execute(
          source =>
            "def double(x):" & LF &
            "" & LF &
            "    " & LF &
            "    return x * 2" & LF &
            ""
        );
        check_equal(integer'(python_call("double", 21)), 42);
        check_equal(integer'(python_call("double", -5)), -10);

      elsif run("persistent namespace across execute and call") then
        python_execute(source => "COUNTER = 0");
        python_execute(
          source =>
            "def inc():" & LF &
            "    global COUNTER" & LF &
            "    COUNTER += 1" & LF &
            "    return COUNTER"
        );
        check_equal(integer'(python_call("inc")), 1);
        check_equal(integer'(python_call("inc")), 2);
        check_equal(integer'(python_call("inc")), 3);

      elsif run("execute file with relative file name") then
        python_execute(file_name => "test/models/reference_model.py");
        check_equal(string'(python_call("get_model_dir")), tb_path(runner_cfg) & "models");

      elsif run("execute file with absolute file name") then
        python_execute(file_name => tb_path(runner_cfg) & "models/reference_model.py");
        check_equal(string'(python_call("get_model_dir")), tb_path(runner_cfg) & "models");

      elsif run("__file__ is set during file execution and restored after") then
        python_execute(file_name => "test/models/reference_model.py");
        check_equal(
          string'(python_call("get_file_during_exec")),
          tb_path(runner_cfg) & "models/reference_model.py"
        );
        python_execute(source => "def after_file_absent():" & LF & "    return '__file__' not in globals()");
        check_true(python_call("after_file_absent"));

      elsif run("sibling import without PYTHONPATH, sys.path restored") then
        python_execute(source => "GAIN = 3");
        python_execute(file_name => "test/models/importer.py");
        check_equal(integer'(python_call("scaled", 2)), 9); -- fir(2) = 3, 3 * GAIN(3) = 9
        check_false(python_call("dir_in_syspath"));

      elsif run("re-executing a file executes it again") then
        python_execute(file_name => "test/models/counter.py");
        python_execute(file_name => "test/models/counter.py");
        check_equal(integer'(python_call("get_call_count")), 2);

      elsif run("python_execute with both source and file_name fails") then
        mock(python_logger, failure);
        python_execute(source => "pass", file_name => "test/models/counter.py");
        check_only_log(python_logger, "python_execute: give either source or file_name, not both", failure);
        unmock(python_logger);

      elsif run("python_execute with missing file fails") then
        -- The error message shows the path in the native format of the OS
        python_execute(source => "import os" & LF & "def native_repr(path):" & LF & "    return repr(os.path.normpath(path))");
        mock(python_logger, failure);
        python_execute(file_name => tb_path(runner_cfg) & "models/does_not_exist.py");
        check_only_log(
          python_logger,
          "python_execute(file_name => """ & tb_path(runner_cfg) & "models/does_not_exist.py"")" &
          " failed:" & LF &
          "FileNotFoundError: [Errno 2] No such file or directory: " &
          string'(python_call("native_repr", string'(tb_path(runner_cfg) & "models/does_not_exist.py"))),
          failure
        );
        unmock(python_logger);

      elsif run("python exception with traceback logs a failure") then
        mock(python_logger, failure);
        python_execute(source => "raise ValueError(""boom"")");
        check_only_log(
          python_logger,
          "python_execute failed:" & LF &
          "Traceback (most recent call last):" & LF &
          "  File ""<python_execute #1>"", line 1, in <module>" & LF &
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
