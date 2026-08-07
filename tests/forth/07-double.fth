( Double-number words: D+ D< DNEGATE, and the unsigned/mixed helpers )
( U* and U/ that produce or consume doubles. )
( A double is two cells with the LOW cell pushed FIRST, so the high cell )
( is on top -- see dpush/dpop in c/forth.c.  The double 1 is "1 0". )
1 0 2 0 D+ 0 ?= 3 ?=
( carry from the low cell into the high cell )
65535 0 1 0 D+ 1 ?= 0 ?=
( negative doubles: -1 is 65535 65535 )
1 0 DNEGATE -1 ?= -1 ?=
1 0 DNEGATE 1 0 D+ 0 ?= 0 ?=
( D< compares signed doubles )
1 0 2 0 D< 1 ?=
2 0 1 0 D< 0 ?=
1 0 1 0 D< 0 ?=
( a large positive double is greater than a small one )
0 1 1 0 D< 0 ?=
1 0 0 1 D< 1 ?=
( U* multiplies two single cells into a double, so it cannot overflow. )
( 1000*1000 = 1000000 = 0x000F4240 -> lo 0x4240 = 16960, hi 0x000F = 15. )
( The high cell ends up on top, and ?= consumes the top first. )
1000 1000 U* 15 ?= 16960 ?=
( and U/ divides that double back down again [ ud u -- rem quot ] )
16960 15 1000 U/ 1000 ?= 0 ?=
( 2DUP on a double is the same as duplicating the cell pair, which is why )
( D+ can consume what 2DUP produced )
1 0 2DUP D+ 0 ?= 2 ?=
5 0 7 0 2OVER D+ 0 ?= 12 ?= 0 ?= 5 ?=
REPORT
