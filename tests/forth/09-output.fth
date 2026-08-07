( Output and number formatting: . U. .S EMIT CR SPACE SPACES TYPE ." )
( <# # #S #> HOLD SIGN BASE HEX DECIMAL COUNT and ? . )
( Most of these only produce side effects, so the checks here confirm the )
( pieces that DO leave testable state, and the visible output is compared )
( by the harness that runs this file [it captures stdout]. )
( --- BASE --- )
DECIMAL BASE @ 10 ?=
( CAREFUL: a literal is read in the CURRENT base, so inside HEX the token )
( "16" means 0x16 = 22.  Compare against 10 [= 0x10 = sixteen] instead, )
( or switch back to DECIMAL before comparing.  This trips everyone once. )
HEX BASE @ 10 ?= DECIMAL
HEX BASE @ DECIMAL 16 ?=
16 BASE ! BASE @ DECIMAL 16 ?=
BASE @ 10 ?=
( In HEX a bare 255 means 0x255, which is 597 decimal -- easy to trip on. )
HEX 255 DECIMAL 597 ?=
HEX FF DECIMAL 255 ?=
( --- pictured numeric output --- )
( NOTE: this implementation is SINGLE-cell [ u -- ], where Forth-79 11.1 )
( specifies a double [ ud -- ].  The tests below pin what it actually does. )
( <# #S #> converts the whole number; the result is addr len for TYPE. )
123 <# #S #> 3 ?= DROP
255 <# # # #> 2 ?= DROP
( HOLD inserts a character; the string builds RIGHT to LEFT )
<# 65 HOLD 66 HOLD #> 2 ?= DROP
( SIGN inserts '-' when the value it is given is negative )
<# 49 HOLD -1 SIGN #> 2 ?= DROP
<# 49 HOLD 1 SIGN #> 1 ?= DROP
( in HEX, #S formats in hex )
HEX FF <# #S #> 2 ?= DROP DECIMAL
( --- COUNT with a real counted string --- )
PAD 3 SWAP C!
PAD 1+ 65 SWAP C!
PAD 2 + 66 SWAP C!
PAD 3 + 67 SWAP C!
PAD COUNT 3 ?= PAD 1+ ?=
( --- side-effect words must at least run without disturbing the stack --- )
( Stash the depth in a variable: comparing DEPTH against a DEPTH left on )
( the stack would count that extra cell and never agree. )
VARIABLE D0
DEPTH D0 !
CR SPACE 3 SPACES
32 EMIT 65 EMIT CR
PAD 1+ 3 TYPE CR
." literal text" CR
123 . CR
65535 U. CR
-1 . CR
1 2 .S CR DROP DROP
VARIABLE VQ 77 VQ ! VQ ? CR
DEPTH D0 @ ?=
REPORT
