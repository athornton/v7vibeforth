# For v7
forth.o: forth.c
	cc -c -O forth.c

forth: forth.o
	cc -o forth forth.o
	strip forth

bedit.o: bedit.c
	cc -c -O bedit.c

bedit: bedit.o
	cc -o bedit bedit.o
	strip bedit

# For the world of 2026
bedit-test: bedit.c
	gcc -std=gnu89 -w -O -Wno-return-mismatch \
-DBEDIT_TEST -o /tmp/bedit_test bedit.c && /tmp/bedit_test

bedit-snap: bedit.c
	gcc -std=gnu89 -w -O -Wno-return-mismatch -DBEDIT_SNAP \
-o /tmp/bedit_snap bedit.c && /tmp/bedit_snap

forth-modern: forth.c
	gcc -std=gnu89 -w -Wno-return-mismatch -O -o forth forth.c

bedit-modern: bedit.c
	gcc -std=gnu89 -w -Wno-return-mismatch \
-Wno-incompatible-function-pointer-types -O -o bedit bedit.c
