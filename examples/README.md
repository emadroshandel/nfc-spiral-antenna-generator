# Examples

Every file here was produced by the command shown. Each design writes four
files: `.dxf`, `.kicad_mod`, `.json` and `.png`.

| Design | Command | L | Notes |
|---|---|---|---|
| `st_reference` | `--length 46 --width 20 --turns 6 --trace 0.24 --gap 0.15 --chip-cap 28.5 --board-margin 1.5` | 2.676 µH | the ST calculator reference geometry, sharp corners |
| `tag_rounded_center` | same, plus `--corner-r auto --term-pos center` | 2.783 µH | concentric fillets, centred terminal, short return bridge |
|   | `--length 40 --width 30 --turns 6 --trace 0.35 --gap 0.25 --corner-r 3 --term-pos center --chip-cap 17` | 2.649 µH | looser rules, cheap 2-layer fab |
| `reader_circular_35` | `--shape circle --length 35 --width 35 --turns 5 --trace 0.5 --gap 0.3` | 1.494 µH | round reader coil |
| `octagon_30` | `--shape octagon --length 30 --width 30 --turns 7 --trace 0.3 --gap 0.2` | 2.570 µH | octagonal, square outline (required) |

Load any `.json` back into the GUI with **Load settings** to reproduce or tweak
that design.
