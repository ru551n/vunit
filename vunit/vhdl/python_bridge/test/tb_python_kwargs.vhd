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

entity tb_python_kwargs is
  generic (runner_cfg : string);
end entity;

architecture tb of tb_python_kwargs is
begin
  main : process
    variable arr : integer_array_t;
    variable value : integer;
    variable u : unsigned(7 downto 0);

    -- The same kw value used in two calls
    procedure bump_twice(options : python_kwargs_t) is
    begin
      -- The function modifies its argument in place, but each call gets a copy
      check_equal(integer'(python_call("bump", kwargs => options)), 5);
      check_equal(integer'(python_call("bump", kwargs => options)), 5);
    end;
  begin
    test_runner_setup(runner, runner_cfg);
    python_execute(
      "def describe(*args, **kwargs):" +
      "    return ','.join(f'{k}={v!r}' for k, v in sorted(kwargs.items()))" +
      "def scale(x, gain=1, offset=0):" +
      "    return x * gain + offset"
    );

    while test_suite loop
      if run("Test scalar keyword argument values") then
        check_equal(
          string'(python_call(
            "describe",
            kwargs => kw("i", -7) & kw("b", true) & kw("s", string'("fast")) & kw("l", 'Z')
          )),
          "b=True,i=-7,l='Z',s='fast'"
        );

      elsif run("Test real keyword arguments are exact") then
        check_equal(
          string'(python_call("describe", kwargs => kw("third", 1.0 / 3.0) & kw("tenth", 0.1))),
          "tenth=0.1,third=0.3333333333333333"
        );

      elsif run("Test vector keyword argument values") then
        check_equal(
          string'(python_call(
            "describe",
            kwargs => kw("v", std_ulogic_vector'("10XZ")) & kw("sgn", to_signed(-3, 4)) & kw("uns", to_unsigned(5, 4))
          )),
          "sgn=-3,uns=5,v='10XZ'"
        );

      elsif run("Test integer_array_t keyword argument") then
        python_execute("def total(*, data): return int(data.sum())");
        arr := new_1d(4);
        for idx in 0 to 3 loop
          set(arr, idx, idx + 1);
        end loop;
        check_equal(integer'(python_call("total", kwargs => kw("data", arr))), 10);

      elsif run("Test positional and keyword arguments together") then
        check_equal(integer'(python_call("scale", 5, kwargs => kw("gain", 3) & kw("offset", 1))), 16);
        check_equal(integer'(python_call("scale", 5, kwargs => kw("offset", 2))), 7);
        check_equal(integer'(python_call("scale", 5)), 5);

      elsif run("Test a kw constant can be used in several calls") then
        python_execute("def bump(*, data):" + "    data += 1" + "    return int(data.sum())");
        arr := new_1d(2);
        set(arr, 0, 1);
        set(arr, 1, 2);
        bump_twice(kw("data", arr));

      elsif run("Test keyword arguments with session and procedure form") then
        python_execute("def to_byte(x, *, offset): return x + offset", session => "other");
        python_call("to_byte", 40, u, kwargs => kw("offset", 2), session => "other");
        check_equal(to_integer(u), 42);

      elsif run("Test duplicate keyword argument fails") then
        mock(python_logger, failure);
        value := python_call("scale", 1, kwargs => kw("gain", 2) & kw("gain", 3));
        check_only_log(
          python_logger,
          "python_call(""scale"") failed:" & LF & "TypeError: keyword argument 'gain' is given more than once",
          failure
        );
        unmock(python_logger);

      elsif run("Test invalid keyword argument name fails") then
        mock(python_logger, failure);
        value := python_call("scale", 1, kwargs => kw("1gain", 2));
        check_only_log(
          python_logger,
          "kw(""1gain"") failed:" & LF & "ValueError: kw: '1gain' is not a valid Python keyword argument name",
          failure
        );
        unmock(python_logger);

      elsif run("Test unexpected keyword argument fails") then
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
