-- This Source Code Form is subject to the terms of the Mozilla Public
-- License, v. 2.0. If a copy of the MPL was not distributed with this file,
-- You can obtain one at http://mozilla.org/MPL/2.0/.
--
-- Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

library vunit_lib;
context vunit_lib.vunit_context;

entity tb_python_pkg is
  generic (runner_cfg : string);
end entity;

architecture tb of tb_python_pkg is
  constant std_ulogic_characters : string(1 to 9) := "UX01ZWLH-";
begin
  main : process
    constant golden : python_session_t := "golden";
    constant fixed_point : python_session_t := "fixed_point";
    variable arr, arr_b, arr_c, arr_d, arr_e, result : integer_array_t;
    variable slv4 : std_ulogic_vector(3 downto 0);
    variable slv9_desc : std_ulogic_vector(8 downto 0) := "UX01ZWLH-";
    variable slv9_asc : std_ulogic_vector(0 to 8) := "UX01ZWLH-";
    variable result_vec9 : std_ulogic_vector(8 downto 0);
    variable s4 : signed(3 downto 0);
    variable s8 : signed(7 downto 0);
    variable u8 : unsigned(7 downto 0);
    variable discard_int, value : integer;

    -- Python functions used by the keyword argument tests
    procedure define_kwargs_helpers is
    begin
      python_execute(
        "def describe(*args, **kwargs):" +
        "    return ','.join(f'{k}={v!r}' for k, v in sorted(kwargs.items()))" +
        "def scale(x, gain=1, offset=0):" +
        "    return x * gain + offset"
      );
    end;

    -- The same kw value used in two calls
    procedure bump_twice(options : python_kwargs_t) is
    begin
      -- The function modifies its argument in place, but each call gets a copy
      check_equal(integer'(python_call("bump", kwargs => options)), 5);
      check_equal(integer'(python_call("bump", kwargs => options)), 5);
    end;
  begin
    test_runner_setup(runner, runner_cfg);

    while test_suite loop

      if run("Test inline source") then
        python_execute(source => "X = 6 * 7");
        python_execute(source => "def get_x():" & LF & "    return X");
        check_equal(integer'(python_call("get_x")), 42);

      elsif run("Test multiline source with blank lines and indentation") then
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

      elsif run("Test that + joins source lines") then
        check_equal(string'("a" + "b"), "a" & LF & "b");
        check_equal(string'("a" + "" + "b"), "a" & LF & LF & "b");
        python_execute(
          "def triple(x):" +
          "" +
          "    return x * 3"
        );
        check_equal(integer'(python_call("triple", 14)), 42);

      elsif run("Test that + on strings does not disturb numeric_std arithmetic") then
        check_equal(to_integer(unsigned'("0011") + "0001"), 4);
        check_equal(to_integer(signed'("1110") + signed'("0001")), -1);

      elsif run("Test persistent namespace across execute and call") then
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

      elsif run("Test executing a file with a relative file name") then
        python_execute(file_name => "test/models/reference_model.py");
        check_equal(string'(python_call("get_model_dir")), tb_path(runner_cfg) & "models");

      elsif run("Test executing a file with an absolute file name") then
        python_execute(file_name => tb_path(runner_cfg) & "models/reference_model.py");
        check_equal(string'(python_call("get_model_dir")), tb_path(runner_cfg) & "models");

      elsif run("Test that __file__ is set during file execution and restored after") then
        python_execute(file_name => "test/models/reference_model.py");
        check_equal(
          string'(python_call("get_file_during_exec")),
          tb_path(runner_cfg) & "models/reference_model.py"
        );
        python_execute(source => "def after_file_absent():" & LF & "    return '__file__' not in globals()");
        check_true(python_call("after_file_absent"));

      elsif run("Test sibling import without PYTHONPATH and that sys.path is restored") then
        python_execute(source => "GAIN = 3");
        python_execute(file_name => "test/models/importer.py");
        check_equal(integer'(python_call("scaled", 2)), 9); -- fir(2) = 3, 3 * GAIN(3) = 9
        check_false(python_call("dir_in_syspath"));

      elsif run("Test that re-executing a file executes it again") then
        python_execute(file_name => "test/models/counter.py");
        python_execute(file_name => "test/models/counter.py");
        check_equal(integer'(python_call("get_call_count")), 2);

      elsif run("Test that python_execute with both source and file_name fails") then
        mock(python_logger, failure);
        python_execute(source => "pass", file_name => "test/models/counter.py");
        check_only_log(python_logger, "python_execute: give either source or file_name, not both", failure);
        unmock(python_logger);

      elsif run("Test that python_execute with a missing file fails") then
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

      elsif run("Test that a Python exception with traceback logs a failure") then
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


      elsif run("Test call without arguments") then
        python_execute(source => "def answer():" & LF & "    return 42");
        check_equal(integer'(python_call("answer")), 42);

      elsif run("Test integer round trip including bounds") then
        python_execute(source => "def identity_int(x):" & LF & "    return x");
        check_equal(integer'(python_call("identity_int", 0)), 0);
        check_equal(integer'(python_call("identity_int", 123456)), 123456);
        check_equal(integer'(python_call("identity_int", integer'low)), integer'low);
        check_equal(integer'(python_call("identity_int", integer'high)), integer'high);

      elsif run("Test real round trip") then
        python_execute(source => "def identity_real(x):" & LF & "    return x");
        check_equal(real'(python_call("identity_real", 0.0)), 0.0);
        check_equal(real'(python_call("identity_real", 3.5)), 3.5);
        check_equal(real'(python_call("identity_real", -2.25)), -2.25);

      elsif run("Test boolean round trip") then
        python_execute(source => "def identity_bool(x):" & LF & "    return x");
        check_true(python_call("identity_bool", true));
        check_false(python_call("identity_bool", false));

      elsif run("Test string round trip including non-ASCII") then
        python_execute(source => "def identity_str(x):" & LF & "    return x");
        check_equal(string'(python_call("identity_str", string'(""))), string'(""));
        check_equal(string'(python_call("identity_str", string'("hello"))), string'("hello"));
        check_equal(string'(python_call("identity_str", string'("café å"))), string'("café å"));

      elsif run("Test std_ulogic round trip of all 9 states") then
        python_execute(source => "def identity_logic(x):" & LF & "    return x");
        for idx in std_ulogic_characters'range loop
          check_equal(
            std_ulogic'(python_call("identity_logic", std_ulogic'val(idx - 1))),
            std_ulogic'val(idx - 1)
          );
        end loop;

      elsif run("Test std_ulogic_vector round trip with descending range") then
        python_execute(source => "def identity_slv(x):" & LF & "    return x");
        result_vec9 := python_call("identity_slv", slv9_desc);
        check_equal(result_vec9, slv9_desc);

      elsif run("Test std_ulogic_vector round trip with ascending range") then
        python_execute(source => "def identity_slv(x):" & LF & "    return x");
        result_vec9 := python_call("identity_slv", slv9_asc);
        check_equal(result_vec9, slv9_asc);

      elsif run("Test signed argument round trip including negative and bounds") then
        python_execute(source => "def identity_int(x):" & LF & "    return x");
        check_equal(integer'(python_call("identity_int", to_signed(-128, 8))), -128);
        check_equal(integer'(python_call("identity_int", to_signed(127, 8))), 127);
        check_equal(integer'(python_call("identity_int", to_signed(-1, 8))), -1);
        check_equal(integer'(python_call("identity_int", to_signed(0, 8))), 0);

      elsif run("Test unsigned argument round trip including bounds") then
        python_execute(source => "def identity_int(x):" & LF & "    return x");
        check_equal(integer'(python_call("identity_int", to_unsigned(0, 8))), 0);
        check_equal(integer'(python_call("identity_int", to_unsigned(255, 8))), 255);

      elsif run("Test signed and unsigned procedure results including exact bounds") then
        python_execute(source => "def identity_int(x):" & LF & "    return x");

        python_call("identity_int", -128, s8);
        check_equal(s8, to_signed(-128, 8));

        python_call("identity_int", 127, s8);
        check_equal(s8, to_signed(127, 8));

        python_call("identity_int", 0, u8);
        check_equal(u8, to_unsigned(0, 8));

        python_call("identity_int", 255, u8);
        check_equal(u8, to_unsigned(255, 8));

      elsif run("Test repeated calls") then
        python_execute(source => "def identity_int(x):" & LF & "    return x");
        for i in 0 to 999 loop
          check_equal(integer'(python_call("identity_int", i)), i);
        end loop;

      elsif run("Test that a wrong return type fails") then
        python_execute(source => "def returns_none():" & LF & "    return None");
        mock(python_logger, failure);
        discard_int := python_call("returns_none");
        check_only_log(
          python_logger,
          "python_call(""returns_none"") failed:" & LF &
          "TypeError: python_call('returns_none'): cannot return Python NoneType (None) as VHDL integer; expected int",
          failure
        );
        unmock(python_logger);

      elsif run("Test that integer overflow fails") then
        python_execute(source => "def too_big():" & LF & "    return 2**31");
        mock(python_logger, failure);
        discard_int := python_call("too_big");
        check_only_log(
          python_logger,
          "python_call(""too_big"") failed:" & LF &
          "OverflowError: python_call('too_big'): 2147483648 is outside the range of VHDL integer " &
          "(-2147483648 to 2147483647)",
          failure
        );
        unmock(python_logger);

      elsif run("Test that signed overflow in a procedure result fails") then
        python_execute(source => "def identity_int(x):" & LF & "    return x");
        mock(python_logger, failure);
        python_call("identity_int", 200, s8);
        check_only_log(
          python_logger,
          "python_call(""identity_int"") failed:" & LF &
          "OverflowError: python_call('identity_int'): 200 does not fit in a 8 bit signed (-128 to 127)",
          failure
        );
        unmock(python_logger);

      elsif run("Test that unsigned overflow in a procedure result fails") then
        python_execute(source => "def identity_int(x):" & LF & "    return x");
        mock(python_logger, failure);
        python_call("identity_int", -1, u8);
        check_only_log(
          python_logger,
          "python_call(""identity_int"") failed:" & LF &
          "OverflowError: python_call('identity_int'): -1 does not fit in a 8 bit unsigned (0 to 255)",
          failure
        );
        unmock(python_logger);

      elsif run("Test that a metavalue in a signed argument fails") then
        python_execute(source => "def identity_int(x):" & LF & "    return x");
        s4 := (3 => '1', 2 => '0', 1 => 'X', 0 => '1');
        mock(python_logger, failure);
        discard_int := python_call("identity_int", s4);
        check_only_log(
          python_logger,
          "python_call(""identity_int"") failed:" & LF &
          "ValueError: Cannot convert signed value ""10X1"" containing metavalues to a Python int",
          failure
        );
        unmock(python_logger);

      elsif run("Test that a std_ulogic_vector length mismatch in a procedure result fails") then
        python_execute(source => "def wrong_length_bits():" & LF & "    return ""01011""");
        mock(python_logger, failure);
        python_call("wrong_length_bits", slv4);
        check_only_log(
          python_logger,
          "python_call(""wrong_length_bits"") failed:" & LF &
          "ValueError: python_call('wrong_length_bits'): returned 5 std_ulogic values but the VHDL result has length 4",
          failure
        );
        unmock(python_logger);

      elsif run("Test that an undefined function fails") then
        mock(python_logger, failure);
        discard_int := python_call("no_such_function");
        check_only_log(
          python_logger,
          "python_call(""no_such_function"") failed:" & LF &
          "NameError: Python function 'no_such_function' is not defined. Define it with python_execute first.",
          failure
        );
        unmock(python_logger);


      elsif run("Test that a 2D array preserves axis orientation, get(a, x, y) is a[y, x]") then
        arr := new_2d(width => 3, height => 2, bit_width => 16, is_signed => false);
        for y in 0 to 1 loop
          for x in 0 to 2 loop
            set(arr, x, y, y * 10 + x);
          end loop;
        end loop;
        python_execute(
          source =>
            "def orientation_ok(a):" & LF &
            "    return bool(a[0, 1] == 1 and a[1, 2] == 12 and a.shape == (2, 3))"
        );
        check_true(python_call("orientation_ok", arr));

      elsif run("Test that a 3D array preserves axis orientation, get(a, x, y, z) is a[y, x, z]") then
        arr := new_3d(width => 3, height => 2, depth => 4, bit_width => 16, is_signed => false);
        for y in 0 to 1 loop
          for x in 0 to 2 loop
            for z in 0 to 3 loop
              set(arr, x, y, z, y * 100 + x * 10 + z);
            end loop;
          end loop;
        end loop;
        python_execute(
          source =>
            "def orientation_ok3(a):" & LF &
            "    return bool(a[0, 1, 2] == 12 and a[1, 2, 3] == 123 and a.shape == (2, 3, 4))"
        );
        check_true(python_call("orientation_ok3", arr));

      elsif run("Test that returned 2D and 3D arrays preserve axis orientation") then
        python_execute(
          source =>
            "import numpy as np" & LF &
            "def make2():" & LF &
            "    return np.array([[10 * y + x for x in range(3)] for y in range(2)], dtype=np.int16)" & LF &
            "def make3():" & LF &
            "    return np.fromfunction(lambda y, x, z: 100 * y + 10 * x + z, (2, 3, 4), dtype=int)"
        );
        result := python_call("make2");
        check_equal(width(result), 3);
        check_equal(height(result), 2);
        check_equal(depth(result), 1);
        check_equal(bit_width(result), 16);
        check_true(is_signed(result));
        for y in 0 to 1 loop
          for x in 0 to 2 loop
            check_equal(get(result, x, y), 10 * y + x);
          end loop;
        end loop;

        result := python_call("make3");
        check_equal(width(result), 3);
        check_equal(height(result), 2);
        check_equal(depth(result), 4);
        for y in 0 to 1 loop
          for x in 0 to 2 loop
            for z in 0 to 3 loop
              check_equal(get(result, x, y, z), 100 * y + 10 * x + z);
            end loop;
          end loop;
        end loop;

      elsif run("Test that metadata is preserved when returning the same array argument") then
        arr := new_1d(length => 4, bit_width => 12, is_signed => true);
        set(arr, 0, -2048);
        set(arr, 1, -1);
        set(arr, 2, 0);
        set(arr, 3, 2047);
        python_execute(source => "def identity(a):" & LF & "    return a");
        result := python_call("identity", arr);
        check_equal(bit_width(result), 12);
        check_true(is_signed(result));
        check_equal(length(result), 4);
        for idx in 0 to 3 loop
          check_equal(get(result, idx), get(arr, idx));
        end loop;

      elsif run("Test that metadata is derived from dtype for a newly created array") then
        arr := new_1d(length => 4, bit_width => 10, is_signed => false);
        set(arr, 0, 0);
        set(arr, 1, 1);
        set(arr, 2, 500);
        set(arr, 3, 1023);
        python_execute(
          source =>
            "import numpy as np" & LF &
            "def to_uint8(a):" & LF &
            "    return (a % 256).astype(np.uint8)"
        );
        result := python_call("to_uint8", arr);
        check_equal(bit_width(result), 8);
        check_false(is_signed(result));
        check_equal(length(result), 4);
        check_equal(get(result, 0), 0);
        check_equal(get(result, 1), 1);
        check_equal(get(result, 2), 500 mod 256);
        check_equal(get(result, 3), 1023 mod 256);

      elsif run("Test call with zero array arguments") then
        python_execute(
          source =>
            "def sum_arrays(*args):" & LF &
            "    total = 0" & LF &
            "    for a in args:" & LF &
            "        total += int(a.sum())" & LF &
            "    return total"
        );
        check_equal(
          integer'(python_call("sum_arrays", args => integer_array_vec_t'(1 to 0 => null_integer_array))),
          0
        );

      elsif run("Test call with one array argument") then
        python_execute(
          source =>
            "def sum_arrays(*args):" & LF &
            "    total = 0" & LF &
            "    for a in args:" & LF &
            "        total += int(a.sum())" & LF &
            "    return total"
        );
        arr := new_1d(length => 3, bit_width => 8, is_signed => false);
        set(arr, 0, 1);
        set(arr, 1, 2);
        set(arr, 2, 3);
        check_equal(integer'(python_call("sum_arrays", arg => arr)), 6);

      elsif run("Test call with two array arguments") then
        python_execute(
          source =>
            "def sum_arrays(*args):" & LF &
            "    total = 0" & LF &
            "    for a in args:" & LF &
            "        total += int(a.sum())" & LF &
            "    return total"
        );
        arr := new_1d(length => 3, bit_width => 8, is_signed => false);
        set(arr, 0, 1);
        set(arr, 1, 2);
        set(arr, 2, 3);
        arr_b := new_1d(length => 2, bit_width => 8, is_signed => false);
        set(arr_b, 0, 10);
        set(arr_b, 1, 20);
        check_equal(integer'(python_call("sum_arrays", args => (arr, arr_b))), 36);

      elsif run("Test call with five array arguments") then
        python_execute(
          source =>
            "def sum_arrays(*args):" & LF &
            "    total = 0" & LF &
            "    for a in args:" & LF &
            "        total += int(a.sum())" & LF &
            "    return total"
        );
        arr := new_1d(length => 1, bit_width => 8, is_signed => false);
        set(arr, 0, 1);
        arr_b := new_1d(length => 1, bit_width => 8, is_signed => false);
        set(arr_b, 0, 2);
        arr_c := new_1d(length => 1, bit_width => 8, is_signed => false);
        set(arr_c, 0, 3);
        arr_d := new_1d(length => 1, bit_width => 8, is_signed => false);
        set(arr_d, 0, 4);
        arr_e := new_1d(length => 1, bit_width => 8, is_signed => false);
        set(arr_e, 0, 5);
        check_equal(integer'(python_call("sum_arrays", args => (arr, arr_b, arr_c, arr_d, arr_e))), 15);

      elsif run("Test returning a new array with a different shape") then
        arr := new_2d(width => 3, height => 2, bit_width => 16, is_signed => false);
        for y in 0 to 1 loop
          for x in 0 to 2 loop
            set(arr, x, y, y * 10 + x);
          end loop;
        end loop;
        python_execute(source => "def flatten(a):" & LF & "    return a.reshape(-1)");
        result := python_call("flatten", arr);
        check_equal(length(result), 6);
        check_equal(bit_width(result), 32);
        check_true(is_signed(result));
        check_equal(get(result, 0), 0);
        check_equal(get(result, 1), 1);
        check_equal(get(result, 2), 2);
        check_equal(get(result, 3), 10);
        check_equal(get(result, 4), 11);
        check_equal(get(result, 5), 12);

      elsif run("Test null array round trip") then
        arr := new_1d(length => 0, bit_width => 16, is_signed => false);
        python_execute(source => "def identity(a):" & LF & "    return a");
        result := python_call("identity", arr);
        check_equal(length(result), 0);

      elsif run("Test that sessions have separate namespaces") then
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

      elsif run("Test scalar keyword argument values") then
        define_kwargs_helpers;
        check_equal(
          string'(python_call(
            "describe",
            kwargs => kw("i", -7) & kw("b", true) & kw("s", string'("fast")) & kw("l", 'Z')
          )),
          "b=True,i=-7,l='Z',s='fast'"
        );

      elsif run("Test real keyword arguments are exact") then
        define_kwargs_helpers;
        check_equal(
          string'(python_call("describe", kwargs => kw("third", 1.0 / 3.0) & kw("tenth", 0.1))),
          "tenth=0.1,third=0.3333333333333333"
        );

      elsif run("Test vector keyword argument values") then
        define_kwargs_helpers;
        check_equal(
          string'(python_call(
            "describe",
            kwargs => kw("v", std_ulogic_vector'("10XZ")) & kw("sgn", to_signed(-3, 4)) & kw("uns", to_unsigned(5, 4))
          )),
          "sgn=-3,uns=5,v='10XZ'"
        );

      elsif run("Test integer_array_t keyword argument") then
        define_kwargs_helpers;
        python_execute("def total(*, data): return int(data.sum())");
        arr := new_1d(4);
        for idx in 0 to 3 loop
          set(arr, idx, idx + 1);
        end loop;
        check_equal(integer'(python_call("total", kwargs => kw("data", arr))), 10);

      elsif run("Test positional and keyword arguments together") then
        define_kwargs_helpers;
        check_equal(integer'(python_call("scale", 5, kwargs => kw("gain", 3) & kw("offset", 1))), 16);
        check_equal(integer'(python_call("scale", 5, kwargs => kw("offset", 2))), 7);
        check_equal(integer'(python_call("scale", 5)), 5);

      elsif run("Test a kw constant can be used in several calls") then
        define_kwargs_helpers;
        python_execute("def bump(*, data):" + "    data += 1" + "    return int(data.sum())");
        arr := new_1d(2);
        set(arr, 0, 1);
        set(arr, 1, 2);
        bump_twice(kw("data", arr));

      elsif run("Test keyword arguments with session and procedure form") then
        define_kwargs_helpers;
        python_execute("def to_byte(x, *, offset): return x + offset", session => "other");
        python_call("to_byte", 40, u8, kwargs => kw("offset", 2), session => "other");
        check_equal(to_integer(u8), 42);

      elsif run("Test duplicate keyword argument fails") then
        define_kwargs_helpers;
        mock(python_logger, failure);
        value := python_call("scale", 1, kwargs => kw("gain", 2) & kw("gain", 3));
        check_only_log(
          python_logger,
          "python_call(""scale"") failed:" & LF & "TypeError: keyword argument 'gain' is given more than once",
          failure
        );
        unmock(python_logger);

      elsif run("Test invalid keyword argument name fails") then
        define_kwargs_helpers;
        mock(python_logger, failure);
        value := python_call("scale", 1, kwargs => kw("1gain", 2));
        check_only_log(
          python_logger,
          "kw(""1gain"") failed:" & LF & "ValueError: kw: '1gain' is not a valid Python keyword argument name",
          failure
        );
        unmock(python_logger);

      elsif run("Test unexpected keyword argument fails") then
        define_kwargs_helpers;
        -- The exact wording of the error comes from Python and depends on its version
        python_execute(
          "import traceback" +
          "def expected_error():" +
          "    try:" +
          "        scale(1, bogus=2)" +
          "    except TypeError as exc:" +
          "        return ''.join(traceback.format_exception_only(type(exc), exc)).rstrip()"
        );
        mock(python_logger, failure);
        value := python_call("scale", 1, kwargs => kw("bogus", 2));
        check_only_log(
          python_logger,
          "python_call(""scale"") failed:" & LF & string'(python_call("expected_error")),
          failure
        );
        unmock(python_logger);
      end if;
    end loop;

    test_runner_cleanup(runner);
  end process;
end architecture;
