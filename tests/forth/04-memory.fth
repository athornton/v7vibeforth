( Memory: @ ! C@ C! B@ B! +! , C, HERE ALLOT PAD FILL MOVE CMOVE )
( COUNT -TRAILING and the VARIABLE/CONSTANT storage they rely on. )
VARIABLE M1
42 M1 ! M1 @ 42 ?=
3 M1 +! M1 @ 45 ?=
-5 M1 +! M1 @ 40 ?=
( cells are 2 bytes, little-endian, via software fetch/store )
0 M1 ! 258 M1 ! M1 C@ 2 ?=
M1 1+ C@ 1 ?=
( C! then C@ round-trips a byte )
M1 65 SWAP C! M1 C@ 65 ?=
( B@ / B! are the Forth-77 spellings of C@ / C! )
M1 66 SWAP B! M1 B@ 66 ?=
( , and C, append to the dictionary and move HERE )
HERE 1234 , HERE SWAP - 2 ?=
HERE 7 C, HERE SWAP - 1 ?=
( What , and C, wrote is readable again.  Note C, appends ONE byte, so )
( after it HERE-2 is NOT the value just stored -- read the byte back. )
HERE 1 - C@ 7 ?=
HERE 9 , HERE 2 - @ 9 ?=
( ALLOT moves HERE by n bytes )
HERE 4 ALLOT HERE SWAP - 4 ?=
HERE -4 ALLOT HERE SWAP - -4 ?=
( PAD is scratch space above the dictionary )
PAD 5 65 FILL PAD C@ 65 ?=
PAD 4 + C@ 65 ?=
( FILL of zero length changes nothing.  Note FILL is [ addr n char -- ]: )
( giving it only two operands underflows and scribbles over memory. )
PAD 100 + 65 SWAP C!
PAD 100 + 0 32 FILL
PAD 100 + C@ 65 ?=
( MOVE copies cells, CMOVE copies bytes )
PAD 20 + 3 66 FILL
PAD 20 + PAD 40 + 3 CMOVE
PAD 40 + C@ 66 ?=
PAD 42 + C@ 66 ?=
( COUNT turns a counted string into addr len )
PAD 60 + 2 SWAP C!
PAD 61 + 65 SWAP C!
PAD 62 + 66 SWAP C!
PAD 60 + COUNT 2 ?= PAD 61 + ?=
( -TRAILING drops trailing blanks from addr len )
PAD 80 + 65 SWAP C!
PAD 81 + 32 SWAP C!
PAD 82 + 32 SWAP C!
PAD 80 + 3 -TRAILING 1 ?= PAD 80 + ?=
REPORT
