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
use vunit_lib.python_pkg.all;

entity tb_python_call_scalars is
  generic (runner_cfg : string);
end entity;

architecture test_fixture of tb_python_call_scalars is
  constant std_ulogic_characters : string(1 to 9) := "UX01ZWLH-";
begin
  main : process
    variable slv4 : std_ulogic_vector(3 downto 0);
    variable slv9_desc : std_ulogic_vector(8 downto 0) := "UX01ZWLH-";
    variable slv9_asc : std_ulogic_vector(0 to 8) := "UX01ZWLH-";
    variable result_vec9 : std_ulogic_vector(8 downto 0);
    variable s4 : signed(3 downto 0);
    variable s8 : signed(7 downto 0);
    variable u8 : unsigned(7 downto 0);
    variable discard_int : integer;
  begin
    test_runner_setup(runner, runner_cfg);

    while test_suite loop

      if run("zero-arg call") then
        python_execute(source => "def answer():" & LF & "    return 42");
        check_equal(integer'(python_call("answer")), 42);

      elsif run("integer round trip including bounds") then
        python_execute(source => "def identity_int(x):" & LF & "    return x");
        check_equal(integer'(python_call("identity_int", 0)), 0);
        check_equal(integer'(python_call("identity_int", 123456)), 123456);
        check_equal(integer'(python_call("identity_int", integer'low)), integer'low);
        check_equal(integer'(python_call("identity_int", integer'high)), integer'high);

      elsif run("real round trip") then
        python_execute(source => "def identity_real(x):" & LF & "    return x");
        check_equal(real'(python_call("identity_real", 0.0)), 0.0);
        check_equal(real'(python_call("identity_real", 3.5)), 3.5);
        check_equal(real'(python_call("identity_real", -2.25)), -2.25);

      elsif run("boolean round trip") then
        python_execute(source => "def identity_bool(x):" & LF & "    return x");
        check_true(python_call("identity_bool", true));
        check_false(python_call("identity_bool", false));

      elsif run("string round trip including non-ascii") then
        python_execute(source => "def identity_str(x):" & LF & "    return x");
        check_equal(string'(python_call("identity_str", string'(""))), string'(""));
        check_equal(string'(python_call("identity_str", string'("hello"))), string'("hello"));
        check_equal(string'(python_call("identity_str", string'("café å"))), string'("café å"));

      elsif run("std_ulogic all 9 states round trip") then
        python_execute(source => "def identity_logic(x):" & LF & "    return x");
        for idx in std_ulogic_characters'range loop
          check_equal(
            std_ulogic'(python_call("identity_logic", std_ulogic'val(idx - 1))),
            std_ulogic'val(idx - 1)
          );
        end loop;

      elsif run("std_ulogic_vector round trip, descending range, all states") then
        python_execute(source => "def identity_slv(x):" & LF & "    return x");
        result_vec9 := python_call("identity_slv", slv9_desc);
        check_equal(result_vec9, slv9_desc);

      elsif run("std_ulogic_vector round trip, ascending range, all states") then
        python_execute(source => "def identity_slv(x):" & LF & "    return x");
        result_vec9 := python_call("identity_slv", slv9_asc);
        check_equal(result_vec9, slv9_asc);

      elsif run("signed argument round trip incl negative and bounds") then
        python_execute(source => "def identity_int(x):" & LF & "    return x");
        check_equal(integer'(python_call("identity_int", to_signed(-128, 8))), -128);
        check_equal(integer'(python_call("identity_int", to_signed(127, 8))), 127);
        check_equal(integer'(python_call("identity_int", to_signed(-1, 8))), -1);
        check_equal(integer'(python_call("identity_int", to_signed(0, 8))), 0);

      elsif run("unsigned argument round trip incl bounds") then
        python_execute(source => "def identity_int(x):" & LF & "    return x");
        check_equal(integer'(python_call("identity_int", to_unsigned(0, 8))), 0);
        check_equal(integer'(python_call("identity_int", to_unsigned(255, 8))), 255);

      elsif run("signed/unsigned procedure result round trip incl exact bounds") then
        python_execute(source => "def identity_int(x):" & LF & "    return x");

        python_call("identity_int", -128, s8);
        check_equal(s8, to_signed(-128, 8));

        python_call("identity_int", 127, s8);
        check_equal(s8, to_signed(127, 8));

        python_call("identity_int", 0, u8);
        check_equal(u8, to_unsigned(0, 8));

        python_call("identity_int", 255, u8);
        check_equal(u8, to_unsigned(255, 8));

      elsif run("repeated calls") then
        python_execute(source => "def identity_int(x):" & LF & "    return x");
        for i in 0 to 999 loop
          check_equal(integer'(python_call("identity_int", i)), i);
        end loop;

      elsif run("wrong return type fails") then
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

      elsif run("integer overflow fails") then
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

      elsif run("signed overflow in procedure result fails") then
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

      elsif run("unsigned overflow in procedure result fails") then
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

      elsif run("metavalue in signed argument fails") then
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

      elsif run("std_ulogic_vector length mismatch in procedure form fails") then
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

      elsif run("undefined function fails") then
        mock(python_logger, failure);
        discard_int := python_call("no_such_function");
        check_only_log(
          python_logger,
          "python_call(""no_such_function"") failed:" & LF &
          "NameError: Python function 'no_such_function' is not defined. Define it with python_execute first.",
          failure
        );
        unmock(python_logger);

      end if;
    end loop;

    test_runner_cleanup(runner);
  end process;
end architecture;
