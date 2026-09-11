-- This Source Code Form is subject to the terms of the Mozilla Public
-- License, v. 2.0. If a copy of the MPL was not distributed with this file,
-- You can obtain one at http://mozilla.org/MPL/2.0/.
--
-- Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

library vunit_lib;
context vunit_lib.vunit_context;

entity tb_python is
  generic(runner_cfg : string);
end entity;

architecture tb of tb_python is
begin
  main : process
    -- The VHDL implementation to be verified: filter each row with taps 1, 2, 1
    impure function vhdl_filter(image : integer_array_t) return integer_array_t is
      constant result : integer_array_t := new_2d(width(image), height(image), bit_width => 16);
      variable acc : integer;
    begin
      for y in 0 to height(image) - 1 loop
        for x in 0 to width(image) - 1 loop
          acc := get(image, x, y);
          if x >= 1 then
            acc := acc + 2 * get(image, x - 1, y);
          end if;
          if x >= 2 then
            acc := acc + get(image, x - 2, y);
          end if;
          set(result, x, y, acc);
        end loop;
      end loop;
      return result;
    end;

    variable image, expected, got : integer_array_t;
    variable answer : integer;
  begin
    test_runner_setup(runner, runner_cfg);

    while test_suite loop
      if run("Test inline Python") then
        -- "+" joins lines of Python source
        python_execute(
          "def fibonacci(n):" +
          "    a, b = 0, 1" +
          "    for _ in range(n):" +
          "        a, b = b, a + b" +
          "    return a"
        );
        answer := python_call("fibonacci", 10);
        check_equal(answer, 55);

      elsif run("Test against Python reference model") then
        -- Relative to the directory of run.py
        python_execute(file_name => "models/reference_model.py");

        image := new_2d(width => 8, height => 4, bit_width => 8, is_signed => false);
        for y in 0 to height(image) - 1 loop
          for x in 0 to width(image) - 1 loop
            set(image, x, y, (17 * x + 29 * y) mod 256);
          end loop;
        end loop;

        expected := python_call("model", image);
        got := vhdl_filter(image);

        check_equal(width(got), width(expected));
        check_equal(height(got), height(expected));
        for y in 0 to height(image) - 1 loop
          for x in 0 to width(image) - 1 loop
            check_equal(get(got, x, y), get(expected, x, y), "x=" & to_string(x) & ", y=" & to_string(y));
          end loop;
        end loop;
      end if;
    end loop;

    test_runner_cleanup(runner);
  end process;
end architecture;
