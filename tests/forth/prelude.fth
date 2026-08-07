( Assertion harness for the interpreter's own test files. )
( Deliberately tiny: it may only use words simple enough to trust -- )
( : ; VARIABLE ! @ 1+ IF ELSE THEN = . and ." -- because everything )
( else is what we are trying to test.  Costs ~226 bytes of dictionary. )
( Each check bumps TN, so a failure reports its own ordinal. )
VARIABLE TN   0 TN !
VARIABLE NF   0 NF !
: T+ TN @ 1+ TN ! ;
: FAIL! NF @ 1+ NF ! ." *** FAIL test " TN @ . CR ;
( ?T : flag --      ; assert the flag is true )
: ?T T+ IF ELSE FAIL! THEN ;
( ?= : got want --  ; assert two cells are equal )
: ?= T+ = IF ELSE FAIL! THEN ;
: REPORT ." tests=" TN @ . ." failed=" NF @ . CR
  NF @ 0= IF ." ALL-OK" ELSE ." HAVE-FAILURES" THEN CR ;
