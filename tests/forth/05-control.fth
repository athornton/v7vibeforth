( Control flow: IF ELSE THEN BEGIN UNTIL END AGAIN WHILE REPEAT )
( DO LOOP +LOOP I J LEAVE EXIT, plus : ; and the compile-time [ ] LITERAL. )
( Forth-79 only requires these to work COMPILED, so each lives in a word. )
: f1 IF 11 ELSE 22 THEN ;
1 f1 11 ?=
0 f1 22 ?=
-1 f1 11 ?=
: f2 IF 33 THEN 44 ;
1 f2 44 ?= 33 ?=
0 f2 44 ?=
( DO ... LOOP runs limit-start times, I is the index )
: f3 0 5 0 DO 1+ LOOP ;
f3 5 ?=
: f4 0 5 0 DO I + LOOP ;
f4 10 ?=
( Empty range still runs the body ONCE: this DO...LOOP tests at the END, )
( so it is a do-while.  Forth-79 specifies exactly that ["the loop is )
( always executed at least once"], unlike later standards where 0 0 DO )
( skips the body entirely.  Worth pinning so a future change notices. )
: f5 0 0 0 DO 1+ LOOP ;
f5 1 ?=
: f5b 0 3 3 DO 1+ LOOP ;
f5b 1 ?=
( +LOOP steps by n )
: f6 0 10 0 DO 1+ 2 +LOOP ;
f6 5 ?=
( counting down with a negative step )
: f7 0 0 5 DO 1+ -1 +LOOP ;
f7 6 ?=
( J reaches the outer index )
: f8 0 2 0 DO 2 0 DO J + LOOP LOOP ;
f8 2 ?=
( LEAVE exits the loop; the body still finishes this iteration )
: f9 0 5 0 DO 1+ DUP 3 = IF LEAVE THEN LOOP ;
f9 3 ?=
( BEGIN ... UNTIL loops until the flag is true )
: f10 0 BEGIN 1+ DUP 3 = UNTIL ;
f10 3 ?=
( END is the Forth-77 spelling of UNTIL )
: f11 0 BEGIN 1+ DUP 3 = END ;
f11 3 ?=
( BEGIN ... WHILE ... REPEAT tests at the top )
: f12 0 BEGIN DUP 3 < WHILE 1+ REPEAT ;
f12 3 ?=
( AGAIN is an infinite loop, so it needs EXIT to get out )
: f13 0 BEGIN 1+ DUP 3 = IF EXIT THEN AGAIN ;
f13 3 ?=
( EXIT leaves the definition early )
: f14 5 EXIT 6 ;
f14 5 ?=
( [ ] drop out of compilation so the arithmetic happens at compile time )
: f15 [ 3 4 + ] LITERAL ;
f15 7 ?=
( nesting )
: f16 0 3 0 DO 3 0 DO 1+ LOOP LOOP ;
f16 9 ?=
REPORT
