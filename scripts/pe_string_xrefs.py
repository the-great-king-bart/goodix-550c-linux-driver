#!/usr/bin/env python3
"""Locate x86-64 code references to strings in a PE file.

This is a small, reproducible static-analysis helper for the original Windows
UMDF driver. It reads the PE in place and never executes or modifies it.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import pefile
from capstone import CS_ARCH_X86, CS_MODE_64, Cs
from capstone.x86 import X86_OP_IMM, X86_OP_MEM, X86_REG_RIP


@dataclass(frozen=True)
class FunctionRange:
    start: int
    end: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pe", type=Path)
    parser.add_argument("needle", nargs="+")
    parser.add_argument("--full-function", action="store_true")
    parser.add_argument("--context", type=int, default=16)
    return parser.parse_args()


def referenced_addresses(instruction) -> set[int]:
    targets: set[int] = set()
    for operand in instruction.operands:
        if operand.type == X86_OP_MEM and operand.mem.base == X86_REG_RIP:
            targets.add(instruction.address + instruction.size + operand.mem.disp)
        elif operand.type == X86_OP_IMM and operand.imm > 0x10000:
            targets.add(operand.imm)
    return targets


def ascii_at(pe: pefile.PE, address: int, limit: int = 100) -> str | None:
    rva = address - pe.OPTIONAL_HEADER.ImageBase
    if rva < 0:
        return None
    try:
        offset = pe.get_offset_from_rva(rva)
    except pefile.PEFormatError:
        return None
    raw = pe.__data__[offset : offset + limit]
    value = raw.split(b"\x00", 1)[0]
    if len(value) < 4 or any(byte < 0x20 or byte > 0x7E for byte in value):
        return None
    return value.decode("ascii")


def main() -> int:
    args = parse_args()
    raw = args.pe.read_bytes()
    pe = pefile.PE(data=raw, fast_load=False)
    image_base = pe.OPTIONAL_HEADER.ImageBase
    text = next(section for section in pe.sections if section.Name.rstrip(b"\x00") == b".text")
    text_va = image_base + text.VirtualAddress

    decoder = Cs(CS_ARCH_X86, CS_MODE_64)
    decoder.detail = True
    instructions = list(decoder.disasm(text.get_data(), text_va))
    index_by_address = {
        instruction.address: index for index, instruction in enumerate(instructions)
    }

    functions = sorted(
        (
            FunctionRange(
                image_base + entry.struct.BeginAddress,
                image_base + entry.struct.EndAddress,
            )
            for entry in getattr(pe, "DIRECTORY_ENTRY_EXCEPTION", [])
        ),
        key=lambda function: function.start,
    )

    for needle_text in args.needle:
        needle = needle_text.encode("utf-8")
        locations: list[int] = []
        cursor = 0
        while (offset := raw.find(needle, cursor)) >= 0:
            locations.append(image_base + pe.get_rva_from_offset(offset))
            cursor = offset + 1

        print(f"[{needle_text}] locations={','.join(hex(value) for value in locations) or 'none'}")
        for instruction in instructions:
            targets = referenced_addresses(instruction)
            is_reference = any(
                location - 16 <= target <= location + len(needle)
                for location in locations
                for target in targets
            )
            if not is_reference:
                continue

            function = next(
                (item for item in functions if item.start <= instruction.address < item.end), None
            )
            if function:
                print(
                    f"xref={instruction.address:#x} function={function.start:#x}-{function.end:#x}"
                )
                start_index = index_by_address.get(
                    function.start, index_by_address[instruction.address]
                )
                end_index = next(
                    (
                        index
                        for index in range(start_index, len(instructions))
                        if instructions[index].address >= function.end
                    ),
                    len(instructions),
                )
            else:
                print(f"xref={instruction.address:#x} function=unknown")
                start_index = index_by_address[instruction.address]
                end_index = start_index + 1

            center = index_by_address[instruction.address]
            if not args.full_function:
                start_index = max(start_index, center - args.context)
                end_index = min(end_index, center + args.context + 1)

            for item in instructions[start_index:end_index]:
                annotation = ""
                for target in referenced_addresses(item):
                    string = ascii_at(pe, target)
                    if string:
                        annotation = f" ; -> {string!r}"
                        break
                marker = ">" if item.address == instruction.address else " "
                print(
                    f"{marker} {item.address:016x}  {item.mnemonic:<8} {item.op_str}{annotation}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
