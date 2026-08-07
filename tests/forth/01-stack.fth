( Stack manipulation: DUP DROP SWAP OVER ROT ?DUP DEPTH PICK ROLL )
( plus the double-cell group 2DUP 2DROP 2SWAP 2OVER. )
1 DUP + 2 ?=
7 DUP DROP 7 ?=
1 2 SWAP 1 ?= 2 ?=
1 2 OVER 1 ?= 2 ?= 1 ?=
1 2 3 ROT 1 ?= 3 ?= 2 ?=
5 ?DUP 5 ?= 5 ?=
0 ?DUP 0 ?=
DEPTH 0 ?=
1 2 3 DEPTH 3 ?= DROP DROP DROP
( PICK: 0 PICK is the top [ n -- ], ROLL rotates the nth up )
11 22 33 0 PICK 33 ?= DROP DROP DROP
11 22 33 2 PICK 11 ?= DROP DROP DROP
11 22 33 2 ROLL 11 ?= 33 ?= 22 ?=
( double-cell words: a double is lo hi with hi on top )
1 2 2DUP 2 ?= 1 ?= 2 ?= 1 ?=
1 2 3 4 2DROP 2 ?= 1 ?=
1 2 3 4 2SWAP 2 ?= 1 ?= 4 ?= 3 ?=
1 2 3 4 2OVER 2 ?= 1 ?= 4 ?= 3 ?= 2 ?= 1 ?=
( they must compile, not just interpret )
: st1 2OVER 2DROP 2SWAP 2DUP ;
11 22 33 44 st1 22 ?= 11 ?= 22 ?= 11 ?= 44 ?= 33 ?=
( Underflow must not crash: pop[] clamps at an empty stack rather than )
( faulting, so 2OVER on nothing yields six zeros [it pushes 6 cells]. )
DEPTH 0 ?=
2OVER DEPTH 6 ?= 2DROP 2DROP 2DROP
REPORT
