( Comparison and logic: < = > 0< 0= 0> NOT U< AND OR XOR INVERT COM )
( Forth-79 leaves 1 for true and 0 for false [standard p.4], NOT -1. )
1 2 < 1 ?=
2 1 < 0 ?=
2 2 < 0 ?=
2 2 = 1 ?=
2 3 = 0 ?=
3 2 > 1 ?=
2 3 > 0 ?=
-1 0< 1 ?=
1 0< 0 ?=
0 0= 1 ?=
5 0= 0 ?=
1 0> 1 ?=
-1 0> 0 ?=
( NOT is identical to 0= in BOTH standards [Forth-79 p.27 says so], )
( so it is a logical test, not a one's complement. )
0 NOT 1 ?=
5 NOT 0 ?=
( U< compares as unsigned: -1 is 65535, so it is NOT less than 2 )
1 2 U< 1 ?=
-1 2 U< 0 ?=
2 -1 U< 1 ?=
( bitwise )
12 10 AND 8 ?=
12 10 OR 14 ?=
12 10 XOR 6 ?=
0 INVERT -1 ?=
-1 INVERT 0 ?=
0 COM -1 ?=
5 INVERT -6 ?=
( flags are usable directly by IF )
: c1 5 5 = IF 1 ELSE 0 THEN ;
c1 1 ?=
REPORT
