"""
Copyright 2019 Google LLC

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

     http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

Parse processor-specific CSR description YAML file and generate a CSR test file.
This test code will utilize every CSR instruction, writing values to the CSR
and then using a prediction function to calculate a reference value that will
be written into another register and compared against the value actually stored
in the CSR at this point, allowing for the test to self-check in order to
determine success or failure.
"""


"""
To install the bitstring library:
  1) sudo apt-get install python3-bitstring OR
  2) pip install bitstring
"""
import sys
import yaml
import argparse
import random
from bitstring import BitArray as bitarray
import pdb

def get_csr_map(csr_file, xlen):
  """
  Parses the YAML file containing CSR descriptions.

  Args:
    csr_file: The CSR YAML file.
    xlen: The current RISC-V ISA bit length.

  Returns:
    A dictionary contining mappings for each CSR, of the form:
    { csr_name : [csr_address, csr_val_bitarray, csr_write_mask_bitarray, csr_read_mask_bitarray] }
  """
  rv_string = "rv{}".format(str(xlen))
  csrs = {}
  with open(csr_file, "r") as c:
    csr_description = yaml.safe_load(c)
    for csr_dict in csr_description:
      csr_name = csr_dict.get("csr")
      csr_address = csr_dict.get("address")
      csr_access = csr_dict.get("read_only")
      assert(rv_string in csr_dict), "The {} CSR must be configured for rv{}".format(csr_name, str(rv))
      csr_value = bitarray(uintbe=0, length=xlen)
      csr_write_mask = []
      #csr_write_mask = bitarray(uintbe=0, length=xlen)
      csr_read_mask = bitarray(uintbe=0, length=xlen)
      csr_field_list = csr_dict.get(rv_string)
      for csr_field_detail_dict in csr_field_list:
        field_type = csr_field_detail_dict.get("type")
        field_val = csr_field_detail_dict.get("reset_val")
        field_msb = csr_field_detail_dict.get("msb")
        field_lsb = csr_field_detail_dict.get("lsb")
        field_size = field_msb - field_lsb + 1
        if field_type != "WPRI":
          val_bitarray = bitarray(uint=field_val, length=field_size)
          mask_bitarray = bitarray(uint=1, length=1) * field_size
          start_pos = xlen - 1 - field_msb
          end_pos = xlen - 1 - field_lsb
          csr_read_mask.overwrite(mask_bitarray, xlen - 1 - field_msb)
          csr_value.overwrite(val_bitarray, xlen - 1 - field_msb)
          access = True if field_type == "R" else False
          csr_write_mask.append([mask_bitarray, (start_pos, end_pos), access])
      csrs.update({csr_name : [csr_address, csr_value, csr_write_mask, csr_read_mask, csr_access]})
  return csrs


def get_rs1_val(iteration, xlen):
  """
  Calculates and returns the 3 test RS1 values that will be used
  to exercise the CSR.

  Args:
    iteration: Integer between 0 and 2 inclusive, indicates which
               test value to return.
    xlen: The currnet RISC-V ISA bit length.

  Returns:
    A bitarray encoding the value that will be written to the CSR to test it.
    Will be one of 3 values:
      1) 0xa5a5...
      2) 0x5a5a...
      3) A randomly generated number
  """
  if iteration == 0:
    return bitarray(hex=f"0x{'a5'*int(xlen/8)}")
  elif iteration == 1:
    return bitarray(hex=f"0x{'5a'*int(xlen/8)}")
  elif iteration == 2:
    val = bitarray(uint=0, length=xlen)
    # Must randomize all 32 bits, due to randomization library limitations
    for i in range(32):
      bit = random.randint(0, 1)
      val.set(bit, i)
    return val


def csr_write(val, csr_val, csr_write_mask):
  """
  Performs a CSR write.

  Args:
    val: A bitarray containing the value to be written.
    csr_val: A bitarray containing the current CSR value.
    csr_write_mask: A bitarray containing the CSR's mask.
  """
  #if val.len == csr_write_mask.len:
  #  csr_val.overwrite(val & csr_write_mask, 0)

  for bitslice in csr_write_mask:
    read_only = bitslice[2]
    start_index = bitslice[1][0]
    end_index = bitslice[1][1]
    length = end_index - start_index + 1
    mask_val = bitslice[0]
    # only write if not read only
    if not read_only:
      val_slice = val[start_index:end_index+1]
      csr_val.overwrite(mask_val & val_slice, start_index)


"""
CSR Read:
  Reads the given CSR, after applying the bitmask
"""
def csr_read(csr_val, csr_read_mask):
  """
  Performs a CSR read.

  Args:
    csr_val: A bitarray containing the current CSR value.
    csr_read_mask: A bitarray containing the CSR's read mask.

  Returns:
    A bitarray of the logical AND of csr_val and csr_read_mask.
  """
  return csr_val & csr_read_mask


def predict_csr_val(csr_op, rs1_val, csr_val, csr_write_mask, csr_read_mask):
  """
  Predicts the CSR reference value, based on the current CSR operation.

  Args:
    csr_op: A string of the CSR operation being performed.
    rs1_val: A bitarray containing the value to be written to the CSR.
    csr_val: A bitarray containing the current value of the CSR.
    csr_write_mask: A bitarray containing the CSR's write mask.
    csr_read_mask: A bitarray containing the CSR's read mask

  Returns:
    A hexadecimal string of the predicted CSR value.
  """
  prediction = None
  # create a zero bitarray to zero extend immediates
  zero = bitarray(uint=0, length=csr_val.len - 5)
  if csr_op == 'csrrw':
    prediction = csr_read(csr_val, csr_read_mask)
    csr_write(rs1_val, csr_val, csr_write_mask)
  elif csr_op == 'csrrs':
    prediction = csr_read(csr_val, csr_read_mask)
    csr_write(rs1_val | prediction, csr_val, csr_write_mask)
  elif csr_op == 'csrrc':
    prediction = csr_read(csr_val, csr_read_mask)
    csr_write((~rs1_val) & prediction, csr_val, csr_write_mask)
  elif csr_op == 'csrrwi':
    prediction = csr_read(csr_val, csr_read_mask)
    zero.append(rs1_val[-5:])
    csr_write(zero, csr_val, csr_write_mask)
  elif csr_op == 'csrrsi':
    prediction = csr_read(csr_val, csr_read_mask)
    zero.append(rs1_val[-5:])
    csr_write(zero | prediction, csr_val, csr_write_mask)
  elif csr_op == 'csrrci':
    prediction = csr_read(csr_val, csr_read_mask)
    zero.append(rs1_val[-5:])
    csr_write((~zero) & prediction, csr_val, csr_write_mask)
  return f"0x{prediction.hex}"


def gen_setup(test_file):
  """
  Generates the setup code for the CSR test.

  Args:
    test_file: the file containing the generated assembly code.
  """
  test_file.write(f".macro init\n")
  test_file.write(f".endm\n")
  test_file.write(f".section .text.init\n")
  test_file.write(f".globl _start\n")
  test_file.write(f".option norvc\n")
  test_file.write(f"j csr_pass\n")
  for i in range(31):
    test_file.write(f"j csr_fail\n")
  test_file.write(f"_start:\n")


def gen_csr_test_fail(test_file):
  """
  Generates code to handle a test failure.
  This code consists of writing 1 to the GP register in an infinite loop.
  The testbench will poll this register at the end of the test to detect failure.

  Args:
    test_File: the file containing the generated assembly test code.
  """
  test_file.write(f"csr_fail:\n")
  test_file.write(f"\tli gp, 2\n")
  test_file.write(f"\tecall\n")
  test_file.write(f"\tj csr_fail\n")


def gen_csr_test_pass(test_file):
  """
  Generates code to handle test success.
  This code consists of writing 2 to the GP register in an infinite loop.
  The testbench will poll this register at the end of the test to detect success.

  Args:
    test_File: the file containing the generated assembly test code.
  """
  test_file.write(f"csr_pass:\n")
  test_file.write(f"\tli gp, 1\n")
  test_file.write(f"\tecall\n")
  test_file.write(f"\tj csr_pass\n")


def gen_csr_instr(csr_map, csr_instructions, xlen, iterations, out):
  """
  Uses the information in the map produced by get_csr_map() to generate
  test CSR instructions operating on the generated random values.

  Args:
    csr_map: The dictionary containing CSR mappings generated by get_csr_map()
    csr_instructions: A list of all supported CSR instructions in string form.
    xlen: The RISC-V ISA bit length.
    iterations: Indicates how many randomized test files will be generated.
    out: A string containing the directory path that the tests will be generated in.

  Returns:
    No explicit return value, but will write the randomized assembly test code
    to the specified number of files.
  """
  for i in range(iterations):
    # pick two GPRs at random to act as source and destination registers
    # for CSR operations
    source_reg, dest_reg = [f"x{i}" for i in random.sample(range(5, 15), 2)]
    csr_list = list(csr_map.keys())
    with open(f"{out}/riscv_csr_test.{i}.S", "w") as csr_test_file:
      gen_setup(csr_test_file)
      for csr in csr_list:
        csr_address, csr_val, csr_write_mask, csr_read_mask, csr_access = csr_map.get(csr)
        csr_test_file.write(f"\t# {csr}\n")
        if csr_access:
          csr_inst = f"\tcsrr {dest_reg}, {csr_address}\n"
          li = f"\tli {source_reg}, {csr_read(csr_val, csr_read_mask)}\n"
          branch = f"\tbne {source_reg}, {dest_reg}, csr_fail\n"
          csr_test_file.write(csr_inst)
          csr_test_file.write(li)
          csr_test_file.write(branch)
        else:
          for op in csr_instructions:
            for i in range(3):
              # hex string
              rand_rs1_val = get_rs1_val(i, xlen)
              # I type CSR instruction
              first_li = ""
              if op[-1] == "i":
                imm = rand_rs1_val[-5:]
                csr_inst = f"\t{op} {dest_reg}, {csr_address}, 0b{imm.bin}\n"
                imm_val = bitarray(uint=0, length=xlen-5)
                imm_val.append(imm)
                predict_li = (f"\tli {source_reg}, "
                  f"{predict_csr_val(op, imm_val, csr_val, csr_write_mask, csr_read_mask)}\n")
              else:
                first_li = f"\tli {source_reg}, 0x{rand_rs1_val.hex}\n"
                csr_inst = f"\t{op} {dest_reg}, {csr_address}, {source_reg}\n"
                #if csr=='mie':
                #  pdb.set_trace()
                predict_li = (f"\tli {source_reg}, "
                  f"{predict_csr_val(op, rand_rs1_val, csr_val, csr_write_mask, csr_read_mask)}\n")
              branch_check = f"\tbne {source_reg}, {dest_reg}, csr_fail\n"
              csr_test_file.write(first_li)
              csr_test_file.write(csr_inst)
              csr_test_file.write(predict_li)
              csr_test_file.write(branch_check)
              """
              We must hardcode in one final CSR check, as the value that has last
              been written to the CSR has not been tested.
              """
              if csr == csr_list[-1] and op == csr_instructions[-1] and i == 2:
                final_csr_read = f"\tcsrr {dest_reg}, {csr_address}\n"
                csrrs_read_mask = bitarray(uint=0, length=xlen)
                final_li = (f"\tli {source_reg}, "
                  f"{predict_csr_val('csrrs', csrrs_read_mask, csr_val, csr_write_mask, csr_read_mask)}\n")
                final_branch_check = f"\tbne {source_reg}, {dest_reg}, csr_fail\n"
                csr_test_file.write(final_csr_read)
                csr_test_file.write(final_li)
                csr_test_file.write(final_branch_check)
      gen_csr_test_pass(csr_test_file)
      gen_csr_test_fail(csr_test_file)


"""
Define command line arguments.
"""
parser = argparse.ArgumentParser()
parser.add_argument("--csr_file", type=str,
        help="The YAML file contating descriptions of all processor supported CSRs")
parser.add_argument("--xlen", type=int, default=32,
        help="Specify the ISA width, e.g. 32 or 64 or 128")
parser.add_argument("--iterations", type=int, default=1,
        help="Specify how many tests to be generated")
parser.add_argument("--out", type=str, default="./",
        help="Specify output directory")
args = parser.parse_args()


"""
A list containing all supported CSR instructions.
"""
csr_ops = ['csrrw', 'csrrs', 'csrrc', 'csrrwi', 'csrrsi', 'csrrci']

gen_csr_instr(get_csr_map(args.csr_file, args.xlen), csr_ops, args.xlen, args.iterations, args.out)
