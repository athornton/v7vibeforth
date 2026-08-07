( Block storage and vocabularies: BLOCK BUFFER UPDATE SAVE-BUFFERS )
( EMPTY-BUFFERS FLUSH LIST LOAD BLK SCR ;S --> plus VOCABULARY )
( DEFINITIONS CONTEXT CURRENT WORDS. )
( This file WRITES to the blocks file in the current directory, so the )
( harness runs it somewhere disposable. )
( --- BLK and SCR are ordinary variables --- )
BLK @ 0 ?=
SCR @ 0 ?=
( --- BLOCK returns a buffer address inside Forth space --- )
0 BLOCK 0> 1 ?=
( two calls for the same block return the same buffer )
0 BLOCK 0 BLOCK ?=
( different blocks get different buffers )
0 BLOCK 1 BLOCK = 0 ?=
( --- write, UPDATE, FLUSH, then read it back --- )
0 BLOCK 65 SWAP C! UPDATE FLUSH
0 BLOCK C@ 65 ?=
( the change survives EMPTY-BUFFERS because FLUSH wrote it out )
EMPTY-BUFFERS
0 BLOCK C@ 65 ?=
( a second block, to prove the buffer pool rotates )
1 BLOCK 66 SWAP C! UPDATE SAVE-BUFFERS
EMPTY-BUFFERS
1 BLOCK C@ 66 ?=
0 BLOCK C@ 65 ?=
( BUFFER hands back a buffer without reading the block first )
2 BUFFER 0> 1 ?=
( --- LOAD interprets the text in a block --- )
( Fill block 3 with "7 7 + " and load it: it must leave 14. )
3 BLOCK 1024 32 FILL
3 BLOCK 55 SWAP C!
3 BLOCK 1+ 32 SWAP C!
3 BLOCK 2 + 55 SWAP C!
3 BLOCK 3 + 32 SWAP C!
3 BLOCK 4 + 43 SWAP C!
UPDATE FLUSH
3 LOAD 14 ?=
( LOAD sets BLK while it runs and restores it after )
BLK @ 0 ?=
( --- vocabularies --- )
VOCABULARY MYVOC
( executing a vocabulary makes it CONTEXT )
CONTEXT @
MYVOC
CONTEXT @ = 0 ?=
FORTH
( DEFINITIONS points CURRENT at CONTEXT, so new words land in MYVOC )
MYVOC DEFINITIONS
: hidden 99 ;
FORTH DEFINITIONS
( from FORTH, the word defined in MYVOC is not visible )
FIND hidden 0 ?=
( but it is visible again from inside MYVOC )
MYVOC
FIND hidden 0= 0 ?=
FORTH
( core words stay visible from a sub-vocabulary, because it chains to FORTH )
MYVOC
2 3 + 5 ?=
FORTH
CONTEXT @ CURRENT @ ?=
REPORT
