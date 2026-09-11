-- This Source Code Form is subject to the terms of the Mozilla Public
-- License, v. 2.0. If a copy of the MPL was not distributed with this file,
-- You can obtain one at http://mozilla.org/MPL/2.0/.
--
-- Copyright (c) 2014-2026, Lars Asplund lars.anders.asplund@gmail.com

library vunit_lib;
context vunit_lib.vunit_context;
use vunit_lib.python_pkg.all;

entity tb_python_call_arrays is
  generic (runner_cfg : string);
end entity;

architecture test_fixture of tb_python_call_arrays is
begin
  main : process
    variable arr, arr_b, arr_c, arr_d, arr_e, result : integer_array_t;
  begin
    test_runner_setup(runner, runner_cfg);

    while test_suite loop

      if run("2D array preserves axis orientation, get(a,x,y) == a[y,x]") then
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

      elsif run("3D array preserves axis orientation, get(a,x,y,z) == a[y,x,z]") then
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

      elsif run("returned 2D and 3D arrays preserve axis orientation") then
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

      elsif run("metadata preserved when returning the same array argument") then
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

      elsif run("metadata derived from dtype for a newly created array") then
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

      elsif run("zero array arguments") then
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

      elsif run("one array argument") then
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

      elsif run("two array arguments") then
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

      elsif run("five array arguments") then
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

      elsif run("returning a new array with a different shape") then
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

      elsif run("null/empty array round trip") then
        arr := new_1d(length => 0, bit_width => 16, is_signed => false);
        python_execute(source => "def identity(a):" & LF & "    return a");
        result := python_call("identity", arr);
        check_equal(length(result), 0);

      end if;
    end loop;

    test_runner_cleanup(runner);
  end process;
end architecture;
