/*
 * bedit.c -- a VT100 full-screen editor for Forth blocks (16x64).
 * K&R C for v7 cc.  Pure grid logic is terminal-free so it can be
 * unit-tested on the host by compiling with -DBEDIT_TEST.
 */
#include <stdio.h>
#include <signal.h>

#define NL   16
#define NC   64
#define BSZ  1024
#define MAXB 32
#define M_CMD 0
#define M_INS 1

char blk[MAXB][BSZ];
int  nblk;
int  cur;
int  crow, ccol;
int  dirty;
int  mode;
char fname[128];
char srch[64], repl[64];
char msg[80];
int  need_redraw;	/* set when the whole screen must be repainted;
			   declared here so early edit ops (del_line,
			   open_line, do_replace) can set it */

/* clear one block to spaces */
blkclr(b)
int b;
{
	int i;
	for (i = 0; i < BSZ; i++) blk[b][i] = ' ';
}

/* pointer to the cell at (r,c) of the current block */
char *cellp(r, c)
int r, c;
{
	return &blk[cur][r*NC + c];
}

/* initialize editor state: one blank block, cursor home */
edinit()
{
	int i;
	for (i = 0; i < MAXB; i++) blkclr(i);
	nblk = 1;
	cur = 0;
	crow = 0;
	ccol = 0;
	dirty = 0;
	mode = M_CMD;
	srch[0] = 0;
	repl[0] = 0;
	msg[0] = 0;
}

#ifdef BEDIT_TEST
int beep_count;			/* tests observe edge beeps */
dobeep() { beep_count++; }
#endif

mv_left()  { if (ccol > 0) ccol--; else dobeep(); }
mv_right() { if (ccol < NC-1) ccol++; else dobeep(); }
mv_up()    { if (crow > 0) crow--; else dobeep(); }
mv_down()  { if (crow < NL-1) crow++; else dobeep(); }
mv_bol()   { ccol = 0; }

/* $ : last non-space column, or 0 if the line is blank */
mv_eol()
{
	int c;
	ccol = 0;
	for (c = NC-1; c >= 0; c--)
		if (*cellp(crow, c) != ' ') { ccol = c; break; }
}

/* insert char c at cursor in current line: shift tail right, lose col 63 */
ins_char(c)
int c;
{
	int j;
	for (j = NC-1; j > ccol; j--)
		*cellp(crow, j) = *cellp(crow, j-1);
	*cellp(crow, ccol) = c;
	dirty = 1;
}

/* delete char under cursor: shift tail left, pad col 63 with space */
del_char()
{
	int j;
	for (j = ccol; j < NC-1; j++)
		*cellp(crow, j) = *cellp(crow, j+1);
	*cellp(crow, NC-1) = ' ';
	dirty = 1;
}

/* replace char under cursor */
rep_char(c)
int c;
{
	*cellp(crow, ccol) = c;
	dirty = 1;
}

/* INSERT-mode printable: insert then advance (beep if line full at cursor) */
type_char(c)
int c;
{
	if (*cellp(crow, NC-1) != ' ') { dobeep(); /* still insert, lose edge */ }
	ins_char(c);
	if (ccol < NC-1) ccol++;
}

/* INSERT backspace: move left, shift tail left over the deleted cell */
bs_char()
{
	if (ccol > 0) { ccol--; del_char(); }
	else dobeep();
}

lnclr(r)
int r;
{
	int c;
	for (c = 0; c < NC; c++) *cellp(r, c) = ' ';
}

copy_line(dst, src)
int dst, src;
{
	int c;
	for (c = 0; c < NC; c++) *cellp(dst, c) = *cellp(src, c);
}

/* dd: clear current line, shift lines below up one, blank line 15 */
del_line()
{
	int r;
	for (r = crow; r < NL-1; r++) copy_line(r, r+1);
	lnclr(NL-1);
	ccol = 0;
	dirty = 1;
	need_redraw = 1;	/* shifts several lines: repaint all */
}

/* o: open a blank line below current, shift lines below down one
   (line 15 falls off), move cursor to the new line, INSERT mode */
open_line()
{
	int r;
	for (r = NL-1; r > crow+1; r--) copy_line(r, r-1);
	if (crow < NL-1) {
		lnclr(crow+1);
		crow++;
	} else {
		lnclr(crow);	/* on last line: just clear it */
	}
	ccol = 0;
	mode = M_INS;
	dirty = 1;
	need_redraw = 1;	/* shifts several lines: repaint all */
}

/* Enter in INSERT: go to col 0 of next line; beep on last line */
cr_line()
{
	if (crow < NL-1) { crow++; ccol = 0; }
	else dobeep();
}

strc(d, s)			/* bounded copy into a 64-byte field */
char *d, *s;
{
	int i;
	for (i = 0; i < 63 && s[i]; i++) d[i] = s[i];
	d[i] = 0;
}

/* does line r contain `pat` (len n) starting at column c0? */
int line_match_at(r, c0, pat, n)
int r, c0, n;
char *pat;
{
	int k;
	if (c0 + n > NC) return 0;
	for (k = 0; k < n; k++)
		if (*cellp(r, c0+k) != pat[k]) return 0;
	return 1;
}

int slen(s) char *s; { int n=0; while (s[n]) n++; return n; }

/* find next occurrence of srch strictly after (crow,ccol), wrapping once,
   per-line only.  On hit set crow/ccol and return 1; else 0. */
int do_search()
{
	int n, steps, total, r, c;
	n = slen(srch);
	if (n == 0) return 0;
	/* begin at the cell after the cursor */
	r = crow; c = ccol + 1;
	if (c >= NC) { c = 0; r++; if (r >= NL) r = 0; }
	total = NL * NC - 1;
	for (steps = 0; steps < total; steps++) {
		if (line_match_at(r, c, srch, n)) { crow=r; ccol=c; return 1; }
		c++;
		if (c >= NC) { c = 0; r++; if (r >= NL) r = 0; }
	}
	return 0;
}

/* replace all non-overlapping matches of srch with repl in one line.
   Length change shifts the line tail; overflow past col 63 is lost
   (sets *trunc).  Returns number replaced in this line. */
int repl_in_line(r, sp, rp, trunc)
int r; char *sp, *rp; int *trunc;
{
	char work[NC+1];
	int sn, rn, i, j, k, count;
	sn = slen(sp); rn = slen(rp);
	if (sn == 0) return 0;
	/* build into work[], up to NC chars, track truncation */
	i = 0; j = 0; count = 0;
	while (i < NC) {
		if (line_match_at(r, i, sp, sn)) {
			for (k = 0; k < rn; k++) {
				if (j < NC) work[j++] = rp[k];
				else *trunc = 1;
			}
			i += sn;
			count++;
		} else {
			if (j < NC) work[j++] = *cellp(r, i);
			else *trunc = 1;
			i++;
		}
	}
	while (j < NC) work[j++] = ' ';
	for (k = 0; k < NC; k++) *cellp(r, k) = work[k];
	return count;
}

/* replace across all 16 lines of the current block; return total count,
   set *trunc if any line overflowed. */
int do_replace(trunc)
int *trunc;
{
	int r, total;
	*trunc = 0;
	total = 0;
	for (r = 0; r < NL; r++)
		total += repl_in_line(r, srch, repl, trunc);
	if (total) { dirty = 1; need_redraw = 1; }  /* may change many lines */
	return total;
}

/* load fname into blk[], set nblk. Absent/empty -> 1 blank block.
   >MAXB blocks: load first MAXB. Returns 0 on ok, -1 on read error. */
int load_file()
{
	int fd, i, r, off;
	for (i = 0; i < MAXB; i++) blkclr(i);
	nblk = 1;
	fd = open(fname, 0);		/* O_RDONLY */
	if (fd < 0) { nblk = 1; return 0; }	/* absent = fresh 1 block */
	i = 0;
	while (i < MAXB) {
		off = 0;
		while (off < BSZ) {
			r = read(fd, &blk[i][off], BSZ - off);
			if (r <= 0) break;
			off += r;
		}
		if (off > 0) {
			int j;
			for (j = off; j < BSZ; j++) blk[i][j] = ' ';
			i++;
		}
		if (off < BSZ) break;		/* short/last block or EOF */
	}
	nblk = (i < 1) ? 1 : i;
	close(fd);
	return 0;
}

/* write blocks 0..nblk-1 to fname. Returns 0 ok, -1 error. */
int save_file()
{
	int fd, i, off, r;
	fd = creat(fname, 0644);
	if (fd < 0) return -1;
	for (i = 0; i < nblk; i++) {
		off = 0;
		while (off < BSZ) {
			r = write(fd, &blk[i][off], BSZ - off);
			if (r <= 0) { close(fd); return -1; }
			off += r;
		}
	}
	close(fd);
	dirty = 0;
	return 0;
}

/* ^F next block; append blank at end up to MAXB. Returns 0 ok, -1 at cap. */
int next_block()
{
	if (cur == nblk-1) {
		if (nblk >= MAXB) return -1;
		blkclr(nblk);
		nblk++;
	}
	cur++;
	crow = 0; ccol = 0;
	return 0;
}

/* ^B previous block. Returns 0 ok, -1 at block 0. */
int prev_block()
{
	if (cur == 0) return -1;
	cur--;
	crow = 0; ccol = 0;
	return 0;
}

/* decoded key pseudo-codes (outside byte range) */
#define K_UP    256
#define K_DOWN  257
#define K_LEFT  258
#define K_RIGHT 259

int wexit;		/* set by ^X (second press) */
int wexec;		/* set by ^E; real loop performs the shell-out */
int pend_x;		/* 'd' seen, waiting for second 'd' */
int pend_r;		/* 'r' seen, waiting for the replacement char */
int exit_arm;		/* first ^X armed the exit-confirm */
int read_arm;		/* first ^R armed the discard-confirm */

/* forward decls for actions that the real build fills in (Task 9);
   in the test build they are stubbed to record intent. */
int prsrch();	/* returns 1 if a search string was entered */
int prrepl();	/* fills srch+repl; returns 1 if confirmed */

/* handle one decoded key. Returns nothing; mutates globals. */
dispatch(k)
int k;
{
	/* control keys work in both modes */
	switch (k) {
	case '\006':  /* ^F */
		if (next_block() < 0) dobeep();
		need_redraw = 1; exit_arm = 0; read_arm = 0; return;
	case '\002':  /* ^B */
		if (prev_block() < 0) dobeep();
		need_redraw = 1; exit_arm = 0; read_arm = 0; return;
	case '\027':  /* ^W */
		if (save_file() == 0) strc(msg, "wrote file");
		else strc(msg, "write failed");
		exit_arm = 0; read_arm = 0; return;
	case '\022':  /* ^R */
		if (dirty && !read_arm) {
			strc(msg, "discard changes? ^R again");
			read_arm = 1; return;
		}
		load_file(); cur = 0; crow = 0; ccol = 0;
		read_arm = 0; exit_arm = 0; need_redraw = 1;
		strc(msg, "reloaded"); return;
	case '\005':  /* ^E */
		wexec = 1; exit_arm = 0; read_arm = 0; return;
	case '\014':  /* ^L */
		need_redraw = 1; return;
	case '\030':  /* ^X */
		if (dirty && !exit_arm) {
			strc(msg, "unsaved -- ^X again to quit, ^W to write");
			exit_arm = 1; return;
		}
		wexit = 1; return;
	}
	exit_arm = 0; read_arm = 0;	/* any other key disarms confirms */

	if (mode == M_INS) {
		if (k == 033) { mode = M_CMD; if (ccol>0) ccol--; return; }
		if (k == '\010' || k == 0177) { bs_char(); return; }  /* BS */
		if (k == '\r' || k == '\n') { cr_line(); return; }
		if (k == '\t') {			/* tab -> spaces to next 8 col */
			do { type_char(' '); } while (ccol % 8 != 0 && ccol < NC-1);
			return;
		}
		if (k == K_LEFT) { mv_left(); return; }
		if (k == K_RIGHT){ mv_right(); return; }
		if (k == K_UP)   { mv_up(); return; }
		if (k == K_DOWN) { mv_down(); return; }
		if (k >= ' ' && k < 0177) { type_char(k); return; }
		dobeep(); return;
	}

	/* COMMAND mode */
	if (pend_r) { rep_char(k); pend_r = 0; return; }
	if (pend_x) {
		pend_x = 0;
		if (k == 'd') { del_line(); return; }
		/* not the second d: fall through to treat k normally */
	}
	switch (k) {
	case 'h': case K_LEFT:  mv_left(); return;
	case 'l': case K_RIGHT: mv_right(); return;
	case 'k': case K_UP:    mv_up(); return;
	case 'j': case K_DOWN:  mv_down(); return;
	case '0': mv_bol(); return;
	case '$': mv_eol(); return;
	case 'i': mode = M_INS; return;
	case 'a': if (ccol < NC-1) ccol++; mode = M_INS; return;
	case 'o': open_line(); return;
	case 'x': del_char(); return;
	case 'd': pend_x = 1; return;
	case 'r': pend_r = 1; return;
	case '/': if (prsrch()) { if (!do_search()) strc(msg,"not found");
	                                 else strc(msg,"found"); }
	          return;
	case 'n': if (srch[0]==0) strc(msg,"no previous search");
	          else if (!do_search()) strc(msg,"not found");
	          else strc(msg,"found");
	          return;
	case 'S': if (prrepl()) {
	              int t, k2; k2 = do_replace(&t);
	              sprintf(msg, "%d replaced%s", k2, t? " (truncated)":"");
	          }
	          return;
	default: dobeep(); return;
	}
}

#ifdef BEDIT_TEST
/* ---- host-only test harness ---- */
int stub_search_ok, stub_replace_ok;
int prsrch()  { return stub_search_ok; }
int prrepl() { return stub_replace_ok; }

int trun, tfail;

check(cond, name)
char *name;
{
	trun++;
	if (!cond) { tfail++; printf("FAIL: %s\n", name); }
	else printf("ok: %s\n", name);
}

/* return current block as a NUL-terminated 1025-byte string for asserts */
char *snap()
{
	static char s[BSZ+1];
	int i;
	for (i = 0; i < BSZ; i++) s[i] = blk[cur][i];
	s[BSZ] = 0;
	return s;
}

main()
{
	edinit();
	check(nblk == 1, "edinit sets nblk=1");
	check(blk[0][0] == ' ', "edinit blanks block 0");
	check(blk[0][BSZ-1] == ' ', "edinit blanks last cell");
	/* movement */
	edinit();
	beep_count = 0;
	mv_right(); mv_right(); mv_down();
	check(ccol == 2 && crow == 1, "move right/down");
	mv_bol();
	check(ccol == 0, "0 -> col 0");
	edinit(); crow = 0; ccol = 0;
	mv_up(); mv_left();
	check(crow == 0 && ccol == 0 && beep_count == 2, "edge beeps");
	edinit();
	*cellp(0,0) = 'a'; *cellp(0,5) = 'z';
	mv_eol();
	check(ccol == 5, "$ -> last non-space col");
	edinit();
	mv_eol();
	check(ccol == 0, "$ on blank line -> col 0");
	ccol = NC-1; mv_right();
	check(ccol == NC-1, "right at col 63 clamps");
	/* insert / delete / replace */
	edinit();
	ccol = 0; ins_char('a'); ins_char('b'); ins_char('c');
	check(blk[0][0]=='c' && blk[0][1]=='b' && blk[0][2]=='a',
	      "ins_char shifts right");
	edinit();
	*cellp(0,0)='a'; *cellp(0,1)='b'; *cellp(0,2)='c'; ccol=0;
	del_char();
	check(blk[0][0]=='b' && blk[0][1]=='c' && blk[0][2]==' ',
	      "del_char shifts left, pads col63");
	edinit();
	*cellp(0,3)='x'; ccol=3; rep_char('Y');
	check(*cellp(0,3)=='Y', "rep_char replaces");
	edinit();
	ccol=0; type_char('h'); type_char('i');
	check(blk[0][0]=='h' && blk[0][1]=='i' && ccol==2,
	      "type_char inserts + advances");
	edinit();
	ccol=0; type_char('X'); ccol=0; bs_char();
	check(ccol==0 && beep_count>=0 && blk[0][0]=='X',
	      "bs_char at col0 beeps, no change beyond");
	edinit();
	*cellp(0,0)='A'; *cellp(0,1)='B'; ccol=1; bs_char();
	check(blk[0][0]=='B' && ccol==0, "bs_char deletes left");
	edinit();
	check(dirty==0, "edinit clears dirty");
	ins_char('z');
	check(dirty==1, "edit sets dirty");
	/* line ops */
	edinit();
	*cellp(0,0)='0'; *cellp(1,0)='1'; *cellp(2,0)='2'; crow=0;
	del_line();
	check(*cellp(0,0)=='1' && *cellp(1,0)=='2' && *cellp(NL-1,0)==' ',
	      "dd shifts lines up");
	edinit();
	*cellp(0,0)='A'; *cellp(1,0)='B'; crow=0;
	open_line();
	check(crow==1 && mode==M_INS && *cellp(1,0)==' ' && *cellp(2,0)=='B',
	      "o opens blank line below, pushes rest down");
	edinit();
	mode=M_INS; crow=0; ccol=5; cr_line();
	check(crow==1 && ccol==0, "Enter -> next line col 0");
	edinit();
	crow=NL-1; beep_count=0; cr_line();
	check(crow==NL-1 && beep_count==1, "Enter on last line beeps");
	edinit();
	{ int r; for (r=0;r<NL;r++) *cellp(r,0) = 'A'+r; }  /* mark each line */
	crow = 0;
	open_line();				/* blank new line 1, push down */
	check(*cellp(0,0)=='A', "o keeps current line");
	check(*cellp(1,0)==' ', "o blanks the opened line");
	check(*cellp(2,0)=='B', "o pushes old line1 to line2");
	check(*cellp(NL-1,0)=='O', "o drops old bottom line (P), bottom now O");
	/* search */
	edinit();
	*cellp(2,3)='f'; *cellp(2,4)='o'; *cellp(2,5)='o';
	strc(srch, "foo");
	crow=0; ccol=0;
	check(do_search()==1 && crow==2 && ccol==3, "search finds foo");
	check(do_search()==0, "search no second foo");
	edinit();
	strc(srch, "zz");
	check(do_search()==0, "search miss returns 0");
	/* replace, same length */
	edinit();
	*cellp(0,0)='c'; *cellp(0,1)='a'; *cellp(0,2)='t';
	strc(srch,"cat"); strc(repl,"dog");
	{ int t; check(do_replace(&t)==1 && *cellp(0,0)=='d' && *cellp(0,2)=='g',
	               "replace cat->dog"); }
	/* replace shorter: tail shifts left, padded */
	edinit();
	*cellp(0,0)='a'; *cellp(0,1)='b'; *cellp(0,2)='c'; *cellp(0,3)='X';
	strc(srch,"abc"); strc(repl,"Q");
	{ int t; do_replace(&t);
	  check(*cellp(0,0)=='Q' && *cellp(0,1)=='X' && *cellp(0,2)==' ',
	        "replace shorter shifts left"); }
	/* replace longer near edge: truncation flag */
	edinit();
	{ int c; for (c=0;c<NC;c++) *cellp(0,c)='x'; }
	*cellp(0,0)='a';
	strc(srch,"a"); strc(repl,"12");
	{ int t; do_replace(&t); check(t==1, "replace longer sets trunc"); }
	/* file round-trip */
	edinit();
	strc(fname, "/tmp/bedit_rt.blk");
	*cellp(0,0)='H'; *cellp(0,1)='i'; nblk=1;
	next_block();                 /* now 2 blocks, on block 1 */
	*cellp(0,0)='B';              /* mark block 1 */
	check(nblk==2 && cur==1, "next_block appends + moves");
	check(save_file()==0, "save ok");
	edinit();                     /* wipe memory */
	strc(fname, "/tmp/bedit_rt.blk");
	check(load_file()==0 && nblk==2, "load restores 2 blocks");
	check(blk[0][0]=='H' && blk[0][1]=='i', "block 0 content survived");
	check(blk[1][0]=='B', "block 1 content survived");
	/* nav bounds */
	cur=0; check(prev_block()==-1, "prev at 0 refused");
	/* cap */
	edinit(); strc(fname,"/tmp/bedit_rt.blk");
	nblk=MAXB; cur=MAXB-1;
	check(next_block()==-1, "next at cap refused");
	/* dispatch: mode transitions + editing via keys */
	edinit();
	dispatch('i'); check(mode==M_INS, "i enters INSERT");
	dispatch('H'); dispatch('i'); check(blk[0][0]=='H' && blk[0][1]=='i',
	                                     "typing in INSERT");
	dispatch(033); check(mode==M_CMD, "ESC leaves INSERT");
	edinit();
	*cellp(0,0)='a'; *cellp(1,0)='b'; crow=0;
	dispatch('d'); dispatch('d');
	check(*cellp(0,0)=='b', "dd via two keys");
	edinit(); *cellp(0,0)='q'; ccol=0;
	dispatch('r'); dispatch('Z');
	check(*cellp(0,0)=='Z', "r<c> replace via keys");
	edinit(); dirty=1; exit_arm=0;
	dispatch('\030'); check(wexit==0 && exit_arm==1, "^X arms when dirty");
	dispatch('\030'); check(wexit==1, "^X twice quits");
	edinit(); dirty=0;
	dispatch('\030'); check(wexit==1, "^X clean quits at once");
	edinit();
	dispatch('\006'); check(cur==1 && nblk==2, "^F next block");
	dispatch('\002'); check(cur==0, "^B prev block");
	edinit(); stub_search_ok=1; *cellp(0,2)='Q'; strc(srch,"Q");
	dispatch('/'); check(crow==0 && ccol==2, "/ search via dispatch");
	printf("\n%d run, %d failed\n", trun, tfail);
	if (tfail == 0) printf("ALL TESTS PASSED\n");
	return tfail ? 1 : 0;
}
#endif

#if !defined(BEDIT_TEST) && !defined(BEDIT_SNAP)
#include <sgtty.h>

struct sgttyb saved_tty;
int raw_on;

raw_mode()
{
	struct sgttyb t;
	ioctl(0, TIOCGETP, &saved_tty);
	t = saved_tty;
	t.sg_flags |= RAW;
	t.sg_flags &= ~ECHO;
	ioctl(0, TIOCSETP, &t);
	raw_on = 1;
}

cooked_mode()
{
	if (raw_on) { ioctl(0, TIOCSETP, &saved_tty); raw_on = 0; }
}

outc(c) int c; { char b; b = c; write(1, &b, 1); }
outs(s) char *s; { while (*s) outc(*s++); }

/* VT100 */
moveto(r, c) int r, c; { printf("\033[%d;%dH", r, c); fflush(stdout); }
clrscr() { printf("\033[2J"); fflush(stdout); }

/* real beep overrides the test counter version */
dobeep() { outc('\007'); }

/* restore terminal on fatal signals */
onsig(sig) int sig; { cooked_mode(); clrscr(); moveto(24,1); exit(1); }

#define EDIT_TOP   2      /* top border row */
#define EDIT_BOT   19     /* bottom border row */
#define TEXT_ROW0  3      /* screen row of block line 0 */
#define TEXT_COL   3      /* screen col of block col 0 */
#define BORDER_L   2
#define BORDER_R   67
#define KEYS_COL   69
#define MODE_ROW   20
#define POS_ROW    21
#define MSG_ROW    22

char *keyguide[] = {
	"KEYS", "arrows move", "^F ^B blk", "^W write", "^R read",
	"^E exec", "^L redraw", "^X exit", "i a insert", "o open",
	"x dd del", "r replace", "/ n find", "S subst", "ESC command", 0
};

draw_border()
{
	int r, c;
	moveto(EDIT_TOP, BORDER_L); outc('+');
	for (c = 0; c < NC; c++) outc('-');
	outc('+');
	for (r = 0; r < NL; r++) {
		moveto(TEXT_ROW0 + r, BORDER_L); outc('|');
		moveto(TEXT_ROW0 + r, BORDER_R); outc('|');
	}
	moveto(EDIT_BOT, BORDER_L); outc('+');
	for (c = 0; c < NC; c++) outc('-');
	outc('+');
}

draw_line(r)		/* redraw one block text line */
int r;
{
	int c;
	moveto(TEXT_ROW0 + r, TEXT_COL);
	for (c = 0; c < NC; c++) outc(*cellp(r, c));
}

draw_keys()
{
	int i;
	for (i = 0; keyguide[i]; i++) {
		moveto(EDIT_TOP + i, KEYS_COL);
		outs(keyguide[i]);
	}
}

draw_status()
{
	moveto(1, 1);
	printf("bedit -- %s   block %d/%d   %s          ",
	       fname, cur, nblk-1, dirty ? "[modified]" : "          ");
	moveto(MODE_ROW, 1);
	printf("%s          ", mode==M_INS ? "-- INSERT --" : "-- COMMAND --");
	moveto(POS_ROW, 1);
	printf("line %d/%d   col %d/%d      ", crow+1, NL, ccol+1, NC);
	moveto(MSG_ROW, 1);
	printf("%-70s", msg);
	fflush(stdout);
}

place_cursor() { moveto(TEXT_ROW0 + crow, TEXT_COL + ccol); }

full_redraw()
{
	int r;
	clrscr();
	draw_border();
	draw_keys();
	for (r = 0; r < NL; r++) draw_line(r);
	draw_status();
	place_cursor();
	need_redraw = 0;
}

int pushback = -1;		/* one-byte read-ahead pushback, -1 = empty */

int getkey()			/* returns a byte, or K_UP..K_RIGHT */
{
	char b;
	int n;
	if (pushback >= 0) { n = pushback; pushback = -1; return n; }
	n = read(0, &b, 1);
	if (n <= 0) return '\030';		/* treat EOF as ^X */
	if (b != 033) return b & 0377;
	/* possible arrow: ESC [ A/B/C/D */
	n = read(0, &b, 1);
	if (n <= 0) return 033;			/* lone ESC */
	if (b != '[') {				/* ESC then some other key:
						   keep that key, don't lose it */
		pushback = b & 0377;
		return 033;
	}
	n = read(0, &b, 1);
	if (n <= 0) return 033;
	switch (b) {
	case 'A': return K_UP;
	case 'B': return K_DOWN;
	case 'C': return K_RIGHT;
	case 'D': return K_LEFT;
	}
	return 033;
}

/* read a line into buf (max n-1) echoed on MSG_ROW after `label`.
   Returns 1 if Enter pressed, 0 if cancelled (ESC/^C). */
int prmpt(label, buf, n)
char *label, *buf; int n;
{
	int i, k;
	i = 0;
	moveto(MSG_ROW, 1);
	printf("%-70s", "");
	moveto(MSG_ROW, 1);
	outs(label);
	fflush(stdout);
	for (;;) {
		k = getkey();
		if (k == '\r' || k == '\n') { buf[i]=0; return 1; }
		if (k == 033 || k == '\003') { buf[i]=0; return 0; }
		if ((k == '\010' || k == 0177) && i > 0) {
			i--; outc('\010'); outc(' '); outc('\010');
			fflush(stdout); continue;
		}
		if (k >= ' ' && k < 0177 && i < n-1) {
			buf[i++] = k; outc(k); fflush(stdout);
		}
	}
}

int prsrch()  { return prmpt("search: ", srch, sizeof srch); }
int prrepl()
{
	if (!prmpt("search: ", srch, sizeof srch)) return 0;
	return prmpt("replace: ", repl, sizeof repl);
}

do_exec()
{
	char tmp[32], cmd[80];
	int fd, r, c, last;
	sprintf(tmp, "/tmp/bed%d", getpid());
	fd = creat(tmp, 0644);
	if (fd < 0) { strc(msg, "cannot make temp"); return; }
	for (r = 0; r < NL; r++) {
		last = -1;
		for (c = NC-1; c >= 0; c--)
			if (*cellp(r,c) != ' ') { last = c; break; }
		for (c = 0; c <= last; c++) write(fd, cellp(r,c), 1);
		write(fd, "\n", 1);
	}
	close(fd);
	cooked_mode();
	clrscr(); moveto(1,1);
	printf("--- exec block %d ---\n", cur); fflush(stdout);
	sprintf(cmd, "forth < %s 2>&1", tmp);
	system(cmd);
	printf("\n-- press any key --"); fflush(stdout);
	raw_mode();
	getkey();
	unlink(tmp);
	need_redraw = 1;
}

main(argc, argv)
int argc; char **argv;
{
	edinit();
	if (argc > 1) strc(fname, argv[1]);
	else strc(fname, "blocks");
	load_file();
	signal(SIGINT, onsig);
	signal(SIGHUP, onsig);
	signal(SIGTERM, onsig);
	raw_mode();
	full_redraw();
	while (!wexit) {
		int k;
		msg[0] = 0;
		k = getkey();
		wexec = 0;
		dispatch(k);
		if (wexec) do_exec();
		if (need_redraw) full_redraw();
		else { draw_line(crow); draw_status(); place_cursor(); }
	}
	cooked_mode();
	clrscr(); moveto(24,1);
	outs("bye\n");
	return 0;
}
#endif

#ifdef BEDIT_SNAP
/* snapshot build compiles neither the test nor the real block, so the
   grid logic's externals must be satisfied by these stubs. */
dobeep() { }
int prsrch()  { return 0; }
int prrepl() { return 0; }

/* Dump the current block as 16 lines of 64 chars between rulers, so a
   known buffer can be diffed against an expected snapshot. No VT100. */
main()
{
	int r, c;
	edinit();
	*cellp(0,0)=':'; *cellp(0,2)='s'; *cellp(0,3)='q';
	printf("+----------------------------------------------------------------+\n");
	for (r = 0; r < NL; r++) {
		putchar('|');
		for (c = 0; c < NC; c++) putchar(blk[0][r*NC+c]);
		printf("|\n");
	}
	printf("+----------------------------------------------------------------+\n");
	return 0;
}
#endif
