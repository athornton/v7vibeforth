( Return stack and the words that ride on it: >R R> R@ I J )
( These only make sense compiled: the return stack is unbalanced at the )
( interpreter's top level, so every check lives inside a definition. )
: r1 5 >R R> ;
r1 5 ?=
: r2 7 >R R@ R> + ;
r2 14 ?=
( >R >R then R> R> restores the ORIGINAL order, it does not reverse: )
( 1 2 >R >R pushes 2 then 1 onto the return stack, so R> R> pops 1 then )
( 2 back.  ?= consumes the TOP first, so check 2 before 1. )
: r3 1 2 >R >R R> R> ;
r3 2 ?= 1 ?=
( >R / R> must not disturb what is below them )
: r4 11 22 >R 33 R> ;
r4 22 ?= 33 ?= 11 ?=
( I is the innermost loop index, J the next one out )
: r5 0 4 0 DO I + LOOP ;
r5 6 ?=
: r6 0 3 1 DO 3 1 DO I J + + LOOP LOOP ;
r6 12 ?=
( R@ inside a loop reads the loop index, since I is the return stack top )
: r7 0 3 0 DO R@ + LOOP ;
r7 3 ?=
REPORT
