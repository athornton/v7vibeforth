/*
 * forth.c -- an indirect-threaded Forth interpreter.
 *
 * Written in K&R C to compile with the v7 Unix "cc" on a PDP-11.
 * No prototypes, no void, no enum -- 1978 vintage only.
 *
 * Model: indirect threaded code.  Forth memory is a byte array mem[].
 * A "cell" is 2 bytes; cells are read/written by software (fetch/store)
 * so we never do a mis-aligned word access -- that would trap the -11.
 * A word's execution token (xt) is the byte address of its code field.
 * The code field holds a small integer selecting a C primitive; the
 * inner interpreter NEXT walks a list of xts, switch-dispatching each.
 */

#include <stdio.h>
#include <setjmp.h>		/* for ABORT/QUIT to unwind to top level */

typedef short cell;		/* 16 bits on the PDP-11 and on modern gcc */

#define MEM   20000		/* bytes of Forth space (fits v7 64K a.out) */
#define STK   256		/* data / return stack depth (cells) */
#define CELL  2
#define SCRATCH 2		/* 2-cell scratch thread for EXECUTE */
#define HOLDB   6		/* pictured-output hold buffer (bytes 6..45) */
#define HOLDT   46		/* one past end of hold buffer (top of it) */
#define DICT0   46		/* dictionary starts here */

/* ---- block / screen storage ----
 * A block is 1024 bytes; a screen is that block seen as 16 lines x 64.
 * Buffers live at the TOP of mem[] so BLOCK returns a real Forth
 * address usable by @ ! C@ C! TYPE.  The dictionary grows up from low
 * memory and must not reach BUFREG (ccomma/comma guard against it). */
#define NBUF   4		/* number of block buffers */
#define BLKSZ  1024
#define BUFREG (MEM - NBUF*BLKSZ)	/* first byte of buffer region */
#define BUFAD(i) (BUFREG + (i)*BLKSZ)	/* address of buffer i */
#define MAXBLK 1000		/* reject absurd block numbers (typos) */
#define IMMED 0x80		/* flag bits packed in the name-length byte */
#define HIDN  0x40
#define LENM  0x1f

/* Header layout (byte offsets from the link field at `latest`):
 *   +0    link cell (CELL bytes)
 *   +CELL flag byte  (IMMED|HIDN|namelen)
 *   +CELL+1 dialect byte  (D_COMMON / D_F77 / D_F79)
 *   +CELL+2 name bytes ...
 * FBYTE and DBYTE name those two, and NAMEOFF the name start. */
#define FBYTE   CELL		/* offset of flag byte from header base */
#define DBYTE   (CELL+1)	/* offset of dialect byte */
#define NAMEOFF (CELL+2)	/* offset of first name char */

/* per-word dialect tag values (stored in the dialect byte) */
#define D_COMMON 0		/* in both standards / dialect-neutral */
#define D_F77    1		/* Forth-77 only */
#define D_F79    2		/* Forth-79 only */

/* ---- primitive identifiers (stored in code fields) ---- */
#define DOCOL   1
#define DOVAR   2
#define DOCON   3
#define P_EXIT  4
#define P_LIT   5
#define P_BRAN  6
#define P_ZBRAN 7
#define P_DOTQ  8
#define P_DODO  9
#define P_LOOP  10
#define P_EXEC  11
#define P_PLOOP 12
#define P_LEAVE 13
#define DOVOC   14		/* code field of a VOCABULARY word */
#define DOCRE   15		/* code field of a CREATEd word (2-cell cf) */

#define P_DUP   20
#define P_DROP  21
#define P_SWAP  22
#define P_OVER  23
#define P_ROT   24
#define P_QDUP  25
#define P_DEPTH 26
#define P_TOR   27
#define P_FROMR 28
#define P_RAT   29
#define P_I     30
#define P_J     31

#define P_ADD   40
#define P_SUB   41
#define P_MUL   42
#define P_DIV   43
#define P_MOD   44
#define P_NEG   45
#define P_ABS   46
#define P_MIN   47
#define P_MAX   48
#define P_1P    49
#define P_1M    50
#define P_AND   51
#define P_OR    52
#define P_XOR   53
#define P_INV   54
#define P_EQ    55
#define P_LT    56
#define P_GT    57
#define P_ZEQ   58
#define P_ZLT   59
#define P_ZGT   60
#define P_STARSL 61
#define P_SLMOD 62

#define P_FETCH 70
#define P_STORE 71
#define P_CFET  72
#define P_CSTOR 73
#define P_COMMA 74
#define P_CCOMM 75
#define P_HERE  76
#define P_ALLOT 77
#define P_PSTOR 78

#define P_EMIT  90
#define P_KEY   91
#define P_CR    92
#define P_SPACE 93
#define P_SPCS  94
#define P_DOT   95
#define P_TYPE  96
#define P_DOTS  97
#define P_WORDS 98
#define P_BYE   99

#define P_COLON 110
#define P_SEMI  111
#define P_CREAT 112
#define P_VAR   113
#define P_CON   114
#define P_TICK  115
#define P_IMMED 116
#define P_LBRAK 117
#define P_RBRAK 118
#define P_HEX   119
#define P_DEC   120
#define P_IF    121
#define P_ELSE  122
#define P_THEN  123
#define P_BEGIN 124
#define P_UNTIL 125
#define P_AGAIN 126
#define P_WHILE 127
#define P_REPT  128
#define P_DO    129
#define P_CLOOP 130
#define P_PSTR 131
#define P_PAREN 132
#define P_BSLSH 133
#define P_CPLP 134

#define P_BLOCK 135
#define P_BUFFR 136
#define P_UPDAT 137
#define P_FLUSH 138
#define P_SAVEB 139
#define P_EMPTB 140
#define P_LIST  141
#define P_LOAD  142
#define P_ARROW 143		/* --> */
#define P_SSTOP 144		/* ;S */

#define P_VOCAB 145		/* VOCABULARY */
#define P_DEFIN 146		/* DEFINITIONS */
#define P_FORGT 147		/* FORGET */
#define P_FORTH 148		/* FORTH (also has code field DOVOC) */

/* ---- Forth-79 required-set completion ---- */
/* Tier 1: single-cell primitives + wrappers */
#define P_2P    150		/* 2+ */
#define P_2M    151		/* 2- */
#define P_ULT   152		/* U< */
#define P_UDOT  153		/* U. */
#define P_SSMOD 154		/* star-slash-mod */
#define P_MOVE  155		/* MOVE (cells) */
#define P_CMOVE 156		/* CMOVE (bytes) */
#define P_FILL  157		/* FILL */
#define P_COUNT 158		/* COUNT */
#define P_DTRAI 159		/* -TRAILING */
#define P_QUEST 160		/* ? */
#define P_PICK  161		/* PICK */
#define P_ROLL  162		/* ROLL */
#define P_PAD   163		/* PAD */
#define P_LITER 167		/* LITERAL (immediate) */
#define P_BCOMP 168		/* [COMPILE] (immediate) */
#define P_COMPL 169		/* COMPILE */

/* Tier 2: pictured numeric output + interpreter words */
#define P_BRAKN 170		/* <# */
#define P_SHARP 171		/* # */
#define P_SHRPS 172		/* #S */
#define P_NBRAK 173		/* #> */
#define P_HOLD  174		/* HOLD */
#define P_SIGN  175		/* SIGN */
#define P_PWORD 176		/* WORD */
#define P_FINDW 177		/* FIND */
#define P_CONVT 178		/* CONVERT */
#define P_EXPCT 179		/* EXPECT */
#define P_QUERY 180		/* QUERY */

/* Tier 3: DOES>, double-number, system */
#define P_DDOES 181		/* DODOES: code field of a DOES>-child word */
#define P_DOES  182		/* DOES> (immediate: compiles P_DORUN) */
#define P_DORUN 191		/* (DOES>) runtime, planted by DOES> in parent */
#define P_DPLUS 183		/* D+ */
#define P_DLT   184		/* D< */
#define P_DNEG  185		/* DNEGATE */
#define P_USTAR 186		/* U* */
#define P_USLAS 187		/* U/ (U/MOD) */
#define P_ABORT 188		/* ABORT */
#define P_QUIT  189		/* QUIT */
#define P_STD79 190		/* 79-STANDARD */

/* ---- global machine state ---- */
char mem[MEM];
cell dstack[STK];
cell rstack[STK];
int  dsp, rsp;			/* stack pointers (next free slot) */
int  ip, w;			/* instruction ptr, current word ptr */
int  here;			/* dictionary pointer (byte addr) */
int  latest;			/* link field of newest word (any vocab) */
int  a_base, a_state;		/* addresses of BASE and STATE cells */

/* vocabularies.  A vocabulary word has code field DOVOC and a 3-cell
   parameter field: [head, parent, vlink].  head = link address of the
   newest word in this vocab (0 = empty); parent = pfa of the vocab it
   chains to on a failed search (FORTH's parent = 0); vlink = pfa of the
   previously-defined vocabulary (FORGET walks this to fix heads). */
int  a_curr;			/* addr of CURRENT cell (holds a vocab pfa) */
int  a_ctxt;			/* addr of CONTEXT cell (holds a vocab pfa) */
int  a_toin;			/* addr of >IN cell (parse offset mirror) */

/* pictured numeric output: the string is built downward in a reserved
   region of mem[] (so #> can hand its address to TYPE).  hp is the
   current fill point, moving down toward HOLDB as digits are added. */
int  hp;
int  voclist;			/* pfa of newest vocabulary */
int  forthpf;			/* pfa of the FORTH vocabulary (the root) */
int  fence;			/* FORGET refuses below here (protects core) */
int  wlink;			/* link-field addr of the word last found */

/* execution tokens we need while compiling */
int xt_lit, xt_exit, xt_bran, xt_zbran, xt_dotq, xt_dodo, xt_loop;
int xt_ploop, xt_comma;

int wflag;			/* flag byte of the word last found */
int wdial;			/* dialect byte of the word last found */
int bflag, bdial, blink;	/* remembered off-dialect match */

/* Active dialect: 0 = native superset (no -s), else D_F77 / D_F79.
   `curdial` tags words as they are defined (set per group in init,
   D_COMMON for user definitions). */
int dialect;
int curdial;

char tib[256];			/* terminal input line buffer */
int  tlen;			/* length of terminal line in tib */
char nbuf[64];			/* scratch for names parsed at run time */

/* generalized parse source: `src` points at tib (terminal) or into
   mem[] (a block buffer during LOAD); tpos is the parse offset. */
char *src;
int  srclen, tpos;

/* block buffer pool state (NBUF buffers at the top of mem[]) */
int  blkfd;			/* fd of the blocks file, -1 if none */
int  bfblk[NBUF];		/* block number held, -1 = empty */
int  bfdrty[NBUF];		/* buffer modified (UPDATE) */
int  bflock[NBUF];		/* locked: in use as interpret source */
int  bfage[NBUF];		/* LRU clock stamp */
int  blkage;			/* monotonically increasing clock */
int  curbuf;			/* buffer last referenced (target of UPDATE) */
int  loadbuf;			/* buffer being interpreted now, -1 = none */
int  a_blk, a_scr;		/* addrs of BLK and SCR variable cells */

cell fetch();			/* forward decls for non-int returns */
cell pop(), rpop();
long lseek();			/* returns a long file offset */
long dpop();			/* pop a double (two cells) as a long */

jmp_buf toplevel;		/* ABORT/QUIT longjmp here */
int  jmpok;			/* nonzero once toplevel is armed */
int  lastcf;			/* code-field addr of last CREATEd word */
int  xt_doesr;			/* xt of the (DOES>) runtime */

/* ------------------------------------------------------------------ */
/* cell-sized software memory access (alignment- and endian-safe)     */

cell fetch(a)
int a;
{
	return (cell)((mem[a] & 0xff) | ((mem[a+1] & 0xff) << 8));
}

store(a, v)
int a;
cell v;
{
	mem[a]   = v & 0xff;
	mem[a+1] = (v >> 8) & 0xff;
}

/* ------------------------------------------------------------------ */
/* stacks                                                             */

push(v)
cell v;
{
	dstack[dsp++] = v;
}

cell pop()
{
	if (dsp <= 0) { dsp = 0; return 0; }
	return dstack[--dsp];
}

rpush(v)
cell v;
{
	rstack[rsp++] = v;
}

cell rpop()
{
	if (rsp <= 0) { rsp = 0; return 0; }
	return rstack[--rsp];
}

/* double-number stack access: a 32-bit value occupies two cells, with
   the high half more accessible (on top), per Forth-79. */
long dpop()
{
	unsigned hi, lo;
	hi = (unsigned)pop() & 0xffff;
	lo = (unsigned)pop() & 0xffff;
	return ((long)hi << 16) | ((long)lo & 0xffffL);
}

dpush(d)
long d;
{
	push((cell)(d & 0xffff));		/* low */
	push((cell)((d >> 16) & 0xffff));	/* high */
}

/* ------------------------------------------------------------------ */
/* raw output -- use write(2) so nothing is buffered behind the wire  */

emit(c)
int c;
{
	char b;
	b = c;
	write(1, &b, 1);
}

outs(s)
char *s;
{
	while (*s) emit(*s++);
}

/* print a signed cell in the current BASE */
printnum(n)
cell n;
{
	char buf[18];		/* 16 binary digits + sign + slack */
	int i, base;
	unsigned u;

	base = fetch(a_base);
	if (base < 2 || base > 36) base = 10;
	if (n < 0) { emit('-'); u = -n; } else u = n;
	u &= 0xffff;
	i = 0;
	do {
		int d;
		d = u % base;
		buf[i++] = d < 10 ? d + '0' : d - 10 + 'a';
		u = u / base;
	} while (u);
	while (i > 0) emit(buf[--i]);
}

/* print an unsigned cell in the current BASE (for U.) */
printu(un)
unsigned un;
{
	char buf[18];
	int i, base;

	un &= 0xffff;			/* a cell is 16 bits on both hosts */
	base = fetch(a_base);
	if (base < 2 || base > 36) base = 10;
	i = 0;
	do {
		int d;
		d = un % base;
		buf[i++] = d < 10 ? d + '0' : d - 10 + 'a';
		un = un / base;
	} while (un);
	while (i > 0) emit(buf[--i]);
}

/* ------------------------------------------------------------------ */
/* dictionary construction                                            */

align()
{
	if (here & 1) here++;
}

comma(v)
cell v;
{
	if (here + CELL > BUFREG) { errs("dictionary full\n"); return; }
	store(here, v);
	here += CELL;
}

ccomma(c)
int c;
{
	if (here >= BUFREG) { errs("dictionary full\n"); return; }
	mem[here++] = c;
}

/* build a header for `name`; leaves here at the code field.
   Links the new word into the CURRENT vocabulary and tags it with the
   current definition dialect `curdial`.  Before the FORTH vocab exists
   (early init) `current` is 0 and we fall back to the flat `latest`
   chain; initvoc() then adopts that chain as FORTH's head. */
header(name)
char *name;
{
	int prev, len, i;

	align();
	if (fetch(a_curr))
		prev = fetch(fetch(a_curr));	/* head of CURRENT vocab */
	else
		prev = latest;			/* pre-vocab flat chain */
	latest = here;
	comma(prev);
	if (fetch(a_curr))
		store(fetch(a_curr), latest);	/* new word is CURRENT's head */
	len = 0;
	while (name[len]) len++;
	ccomma(len & LENM);		/* +CELL   : flag byte */
	ccomma(curdial);		/* +CELL+1 : dialect byte */
	for (i = 0; i < len; i++) ccomma(name[i]);
	align();
}

/* define a primitive word; return its xt (code field address) */
int prim(name, id)
char *name;
int id;
{
	int xt;
	header(name);
	xt = here;
	comma(id);
	return xt;
}

/* ------------------------------------------------------------------ */
/* case-insensitive dictionary search; sets wflag                     */

int upc(c)
int c;
{
	if (c >= 'a' && c <= 'z') c -= 32;
	return c & 0xff;
}

/* Search a single vocabulary's link chain (head = link addr of newest
   word) for `name`.  On a dialect-compatible hit, set wflag/wdial/wlink
   and return the code-field addr immediately.  On an off-dialect hit,
   remember it in best/bflag/bdial/blink but keep looking.  Returns 0 if
   no dialect-compatible match in this chain; the caller inspects `best`
   (via the globals) after walking the whole search order. */
int findin(name, nlen, head)
char *name;
int nlen, head;
{
	int p, len, i, flag, dia;

	p = head;
	while (p) {
		flag = mem[p + FBYTE] & 0xff;
		len = flag & LENM;
		if (!(flag & HIDN) && len == nlen) {
			for (i = 0; i < len; i++)
				if (upc(mem[p+NAMEOFF+i]) != upc(name[i]))
					break;
			if (i == len) {
				dia = mem[p + DBYTE] & 0xff;
				i = p + NAMEOFF + len;
				if (i & 1) i++;		/* code field addr */
				if (dia == D_COMMON || dialect == 0 ||
				    dia == dialect) {
					wflag = flag;
					wdial = dia;
					wlink = p;
					return i;
				}
				if (blink == 0) {	/* remember off-dialect */
					blink = p;
					bflag = flag;
					bdial = dia;
				}
			}
		}
		p = fetch(p);
	}
	return 0;
}

/* Search order: CONTEXT vocabulary, then follow its parent chain up to
   FORTH.  Sets wflag/wdial/wlink; keeps lenient dialect behaviour. */
int find(name)
char *name;
{
	int nlen, voc, xt, off;

	nlen = 0;
	while (name[nlen]) nlen++;
	blink = 0;
	voc = fetch(a_ctxt);
	while (voc) {
		xt = findin(name, nlen, fetch(voc));	/* voc's head cell */
		if (xt) return xt;
		voc = fetch(voc + CELL);		/* parent pfa */
	}
	if (blink) {			/* only an off-dialect match existed */
		wflag = bflag;
		wdial = bdial;
		wlink = blink;
		off = blink + NAMEOFF + (bflag & LENM);
		if (off & 1) off++;
		return off;
	}
	return 0;
}

/* ------------------------------------------------------------------ */
/* input: pull one whitespace-delimited word from tib into buf        */

int refill()
{
	int c;
	tlen = 0;
	c = getchar();
	if (c == EOF) return 0;
	while (c != EOF && c != '\n') {
		if (tlen < 255) tib[tlen++] = c;
		c = getchar();
	}
	tib[tlen] = 0;
	src = tib;			/* parse from the terminal line */
	srclen = tlen;
	tpos = 0;
	store(a_toin, 0);
	return 1;
}

int white(c)
int c;
{
	return c==' ' || c=='\t' || c=='\r' || c=='\n';
}

int getword(buf)
char *buf;
{
	int n;
	tpos = fetch(a_toin);		/* honor any >IN ! since last call */
	while (tpos < srclen && white(src[tpos]))
		tpos++;
	n = 0;
	while (tpos < srclen && !white(src[tpos])) {
		if (n < 63) buf[n++] = src[tpos];
		tpos++;
	}
	buf[n] = 0;
	store(a_toin, tpos);		/* publish new parse offset to >IN */
	return n;
}

/* parse text[] as a signed number in the current base; ok flag out */
int number(s, out)
char *s;
cell *out;
{
	int base, neg, any, d, c;
	long v;			/* accumulate wide, then truncate */

	base = fetch(a_base);
	if (base < 2 || base > 36) base = 10;
	neg = 0;
	if (*s == '-') { neg = 1; s++; }
	v = 0;
	any = 0;
	while ((c = *s++) != 0) {
		if (c >= '0' && c <= '9') d = c - '0';
		else if (c >= 'a' && c <= 'z') d = c - 'a' + 10;
		else if (c >= 'A' && c <= 'Z') d = c - 'A' + 10;
		else return 0;
		if (d >= base) return 0;
		v = v * base + d;
		any = 1;
	}
	if (!any) return 0;
	if (neg) v = -v;
	*out = (cell)v;
	return 1;
}

/* ------------------------------------------------------------------ */
/* compilation helpers used by : and the control-structure words      */

compile(xt)
int xt;
{
	comma(xt);
}

/* string-literal helper for ."  : copy up to a quote from input.
   Reads/updates the >IN cell so it composes with getword's syncing. */
int parsestr(buf)
char *buf;
{
	int n;
	tpos = fetch(a_toin);
	/* skip one leading blank after ." */
	if (tpos < srclen && src[tpos]==' ') tpos++;
	n = 0;
	while (tpos < srclen && src[tpos] != '"') {
		if (n < 63) buf[n++] = src[tpos];
		tpos++;
	}
	if (tpos < srclen) tpos++;		/* skip closing quote */
	buf[n] = 0;
	store(a_toin, tpos);
	return n;
}

/* ------------------------------------------------------------------ */
/* block / screen storage                                             */

/* open (creating if needed) the blocks file; blkfd<0 = disabled */
openblk(name)
char *name;
{
	int fd, i;
	blkfd = open(name, 2);			/* 2 = O_RDWR on v7 */
	if (blkfd < 0) {
		fd = creat(name, 0666);
		if (fd >= 0) close(fd);
		blkfd = open(name, 2);
	}
	if (blkfd < 0)
		errs("warning: cannot open blocks file\n");
	for (i = 0; i < NBUF; i++) {
		bfblk[i] = -1;
		bfdrty[i] = 0;
		bflock[i] = 0;
		bfage[i] = 0;
	}
	blkage = 0;
	curbuf = -1;
	loadbuf = -1;
}

/* read block u into mem[addr..]; short/absent reads pad with blanks */
rdblk(u, addr)
int u, addr;
{
	int i, n;
	n = 0;
	if (blkfd >= 0) {
		lseek(blkfd, (long)u * (long)BLKSZ, 0);
		n = read(blkfd, &mem[addr], BLKSZ);
		if (n < 0) n = 0;
	}
	for (i = n; i < BLKSZ; i++)
		mem[addr + i] = ' ';
}

/* write mem[addr..addr+1024) back to block u */
wrblk(u, addr)
int u, addr;
{
	if (blkfd < 0) { errs("no blocks file\n"); return; }
	lseek(blkfd, (long)u * (long)BLKSZ, 0);
	write(blkfd, &mem[addr], BLKSZ);
}

/* ensure block u occupies a buffer; if `doread`, load it from disk,
   else just assign a blank buffer.  Returns the buffer's mem address,
   or -1 if every buffer is locked.  Sets curbuf. */
int assign(u, doread)
int u, doread;
{
	int i, v;

	for (i = 0; i < NBUF; i++)		/* already resident? */
		if (bfblk[i] == u) {
			curbuf = i;
			bfage[i] = ++blkage;
			return BUFAD(i);
		}
	v = -1;					/* pick an empty unlocked buffer */
	for (i = 0; i < NBUF; i++)
		if (bfblk[i] < 0 && !bflock[i]) { v = i; break; }
	if (v < 0)				/* else the oldest unlocked one */
		for (i = 0; i < NBUF; i++)
			if (!bflock[i] && (v < 0 || bfage[i] < bfage[v]))
				v = i;
	if (v < 0) { errs("no free block buffer\n"); return -1; }
	if (bfblk[v] >= 0 && bfdrty[v])		/* flush victim if dirty */
		wrblk(bfblk[v], BUFAD(v));
	if (doread) rdblk(u, BUFAD(v));
	else { int j; for (j = 0; j < BLKSZ; j++) mem[BUFAD(v)+j] = ' '; }
	bfblk[v] = u;
	bfdrty[v] = 0;
	bfage[v] = ++blkage;
	curbuf = v;
	return BUFAD(v);
}

/* write all dirty buffers back to disk (SAVE-BUFFERS) */
saveb()
{
	int i;
	for (i = 0; i < NBUF; i++)
		if (bfblk[i] >= 0 && bfdrty[i]) {
			wrblk(bfblk[i], BUFAD(i));
			bfdrty[i] = 0;
		}
}

/* discard all unlocked buffers without writing (EMPTY-BUFFERS) */
emptyb()
{
	int i;
	for (i = 0; i < NBUF; i++)
		if (!bflock[i]) { bfblk[i] = -1; bfdrty[i] = 0; }
}

/* display screen u as 16 numbered lines of 64 characters */
listbl(u)
int u;
{
	int a, r, c, ch;
	store(a_scr, u);
	a = assign(u, 1);
	if (a < 0) return;
	outs("scr # "); printnum(u); emit('\n');
	for (r = 0; r < 16; r++) {
		if (r < 10) emit(' ');
		printnum(r);
		emit(' ');
		for (c = 0; c < 64; c++) {
			ch = mem[a + r*64 + c] & 0xff;
			emit(ch < ' ' || ch > 126 ? ' ' : ch);
		}
		emit('\n');
	}
}

/* WORD ( char -- addr ): parse the next token from the input source up
   to delimiter `char`, lay it down at HERE as a counted string (length
   byte then chars), and return that address.  Advances >IN. */
int parsew(delim)
int delim;
{
	int p, len, dst;
	p = fetch(a_toin);
	while (p < srclen && (src[p] & 0xff) == delim) p++;  /* skip leading */
	dst = here;
	len = 0;
	while (p < srclen && (src[p] & 0xff) != delim) {
		mem[dst + 1 + len] = src[p];
		len++; p++;
	}
	if (p < srclen) p++;			/* consume delimiter */
	mem[dst] = len;
	mem[dst + 1 + len] = ' ';		/* trailing blank (79) */
	store(a_toin, p);
	return dst;
}

/* EXPECT ( addr n -- ): read up to n chars from stdin into addr. */
doexpect(addr, n)
int addr, n;
{
	int c, i;
	i = 0;
	while (i < n) {
		c = getchar();
		if (c == EOF || c == '\n') break;
		mem[addr + i] = c;
		i++;
	}
	if (i < n) mem[addr + i] = 0;
}

/* ------------------------------------------------------------------ */
/* the inner interpreter: run the word whose xt is `x` to done        */

execute(x)
int x;
{
	int pr, n, i, len;
	cell a, b, c;
	int save_ip;
	unsigned ua, ub;		/* for unsigned ops (U< U. U* U/) */
	long la, lb;			/* for double-number ops */

	/* execute() is re-entrant: LOAD runs interp1() which calls execute()
	   again, and all share the global ip and the mem[SCRATCH] thread.
	   The scratch's cell[0] (=x) is read only once here before any
	   recursion, and cell[1] is always xt_exit (re-planted identically by
	   every call), so the ONLY shared state that would corrupt an outer
	   call is ip.  Save it on entry, restore it on every return. */
	save_ip = ip;
	store(SCRATCH, x);
	store(SCRATCH + CELL, xt_exit);
	ip = SCRATCH;
	rpush(0);

	for (;;) {
		w = fetch(ip);
		ip += CELL;
	dispatch:
		pr = fetch(w);
		switch (pr) {

		case DOCOL:
			rpush(ip);
			ip = w + CELL;
			break;
		case P_EXIT:
			ip = rpop();
			if (ip == 0) { ip = save_ip; return; }
			break;
		case DOVAR:
			push(w + CELL);
			break;
		case DOCON:
			push(fetch(w + CELL));
			break;
		case DOVOC:		/* executing a vocab: make it CONTEXT */
			store(a_ctxt, w + CELL);
			break;
		case DOCRE:		/* CREATEd word: push pfa (after 2-cell cf) */
			push(w + 2*CELL);
			break;
		case P_DDOES:		/* DOES> child: push pfa, run does-code */
			push(w + 2*CELL);	/* pfa */
			rpush(ip);
			ip = fetch(w + CELL);	/* the compiled DOES> thread */
			break;
		case P_DORUN:		/* (DOES>) runtime, inside a defining word:
					   patch last CREATEd word, then return */
			store(lastcf, P_DDOES);	/* child cfa -> DODOES */
			store(lastcf + CELL, ip);	/* does-code = here in parent */
			ip = rpop();			/* EXIT the defining word */
			if (ip == 0) { ip = save_ip; return; }
			break;
		case P_LIT:
			push(fetch(ip));
			ip += CELL;
			break;
		case P_BRAN:
			ip = fetch(ip);
			break;
		case P_ZBRAN:
			if (pop() == 0) ip = fetch(ip);
			else ip += CELL;
			break;
		case P_EXEC:
			w = pop();
			goto dispatch;

		case P_DOTQ:
			len = mem[ip] & 0xff;
			for (i = 0; i < len; i++) emit(mem[ip+1+i]);
			ip += 1 + len;
			if (ip & 1) ip++;
			break;

		case P_DUP:
			a = pop(); push(a); push(a);
			break;
		case P_DROP:
			pop();
			break;
		case P_SWAP:
			a = pop(); b = pop(); push(a); push(b);
			break;
		case P_OVER:
			a = pop(); b = pop();
			push(b); push(a); push(b);
			break;
		case P_ROT:
			c = pop(); b = pop(); a = pop();
			push(b); push(c); push(a);
			break;
		case P_QDUP:
			a = pop(); push(a); if (a) push(a);
			break;
		case P_DEPTH:
			push(dsp);
			break;
		case P_TOR:
			rpush(pop());
			break;
		case P_FROMR:
			push(rpop());
			break;
		case P_RAT:
			push(rsp > 0 ? rstack[rsp-1] : 0);
			break;
		case P_I:
			push(rsp > 0 ? rstack[rsp-1] : 0);
			break;
		case P_J:		/* index of next-outer DO loop */
			push(rsp > 2 ? rstack[rsp-3] : 0);
			break;

		case P_ADD:
			push(pop() + pop());
			break;
		case P_SUB:
			a = pop(); b = pop(); push(b - a);
			break;
		case P_MUL:
			push(pop() * pop());
			break;
		case P_DIV:
			a = pop(); b = pop(); push(b / a);
			break;
		case P_MOD:
			a = pop(); b = pop(); push(b % a);
			break;
		case P_NEG:
			push(-pop());
			break;
		case P_ABS:
			a = pop(); push(a < 0 ? -a : a);
			break;
		case P_MIN:
			a = pop(); b = pop(); push(a < b ? a : b);
			break;
		case P_MAX:
			a = pop(); b = pop(); push(a > b ? a : b);
			break;
		case P_1P:
			push(pop() + 1);
			break;
		case P_1M:
			push(pop() - 1);
			break;
		case P_AND:
			push(pop() & pop());
			break;
		case P_OR:
			push(pop() | pop());
			break;
		case P_XOR:
			push(pop() ^ pop());
			break;
		case P_INV:
			push(~pop());
			break;
		case P_EQ:
			push(pop() == pop() ? 1 : 0);
			break;
		case P_LT:
			a = pop(); b = pop(); push(b < a ? 1 : 0);
			break;
		case P_GT:
			a = pop(); b = pop(); push(b > a ? 1 : 0);
			break;
		case P_ZEQ:
			push(pop() == 0 ? 1 : 0);
			break;
		case P_ZLT:
			push(pop() < 0 ? 1 : 0);
			break;
		case P_ZGT:
			push(pop() > 0 ? 1 : 0);
			break;
		case P_STARSL:		/* star-slash: m n p -- (m*n)/p */
			c = pop(); b = pop(); a = pop();
			push((cell)(((long)a * (long)b) / (long)c));
			break;
		case P_SLMOD:		/* /MOD : m n -- rem quot */
			a = pop(); b = pop();
			push(b % a); push(b / a);
			break;
		case P_SSMOD:		/* star-slash-mod: m n p -- rem quot */
			c = pop(); b = pop(); a = pop();
			la = (long)a * (long)b;
			push((cell)(la % (long)c));
			push((cell)(la / (long)c));
			break;
		case P_2P:		/* 2+ */
			push(pop() + 2);
			break;
		case P_2M:		/* 2- */
			push(pop() - 2);
			break;
		case P_ULT:		/* U< : unsigned less-than */
			ua = (unsigned)pop(); ub = (unsigned)pop();
			push(ub < ua ? 1 : 0);
			break;

		case P_FETCH:
			push(fetch(pop()));
			break;
		case P_STORE:
			a = pop(); b = pop(); store(a, b);
			break;
		case P_PSTOR:		/* +! : n addr -- */
			a = pop(); b = pop(); store(a, fetch(a) + b);
			break;
		case P_CFET:
			a = pop(); push(mem[a] & 0xff);
			break;
		case P_CSTOR:
			a = pop(); b = pop(); mem[a] = b & 0xff;
			break;
		case P_COMMA:
			comma(pop());
			break;
		case P_CCOMM:
			ccomma(pop());
			break;
		case P_HERE:
			push(here);
			break;
		case P_ALLOT:
			here += pop();
			break;
		case P_MOVE:		/* from to count -- (cells) */
			n = pop(); b = pop(); a = pop();
			for (i = 0; i < n; i++)
				store(b + i*CELL, fetch(a + i*CELL));
			break;
		case P_CMOVE:		/* from to count -- (bytes) */
			n = pop(); b = pop(); a = pop();
			for (i = 0; i < n; i++)
				mem[b + i] = mem[a + i];
			break;
		case P_FILL:		/* addr count char -- */
			c = pop(); n = pop(); a = pop();
			while (n-- > 0) mem[a++] = c;
			break;
		case P_COUNT:		/* addr -- addr+1 len */
			a = pop();
			push(a + 1);
			push(mem[a] & 0xff);
			break;
		case P_DTRAI:	/* addr n -- addr n' (drop trailing bl) */
			n = pop(); a = pop();
			while (n > 0 && (mem[a+n-1] & 0xff) == ' ') n--;
			push(a); push(n);
			break;
		case P_QUEST:		/* addr -- ; print contents */
			printnum(fetch(pop()));
			emit(' ');
			break;
		case P_PICK:		/* n -- val ; 0 PICK = DUP */
			n = pop();
			if (n < 0 || n >= dsp) push(0);
			else push(dstack[dsp-1-n]);
			break;
		case P_ROLL:		/* n -- ; rotate n+1 items */
			n = pop();
			if (n > 0 && n < dsp) {
				a = dstack[dsp-1-n];
				for (i = dsp-1-n; i < dsp-1; i++)
					dstack[i] = dstack[i+1];
				dstack[dsp-1] = a;
			}
			break;
		case P_PAD:		/* -- addr ; scratch area above HERE */
			push(here + 84);
			break;

		/* --- block / screen words --- */
		case P_BLOCK:		/* u -- addr */
			a = pop();
			push(assign(a, 1));
			break;
		case P_BUFFR:		/* u -- addr */
			a = pop();
			push(assign(a, 0));
			break;
		case P_UPDAT:		/* -- ; mark last buffer dirty */
			if (curbuf >= 0) bfdrty[curbuf] = 1;
			break;
		case P_SAVEB:		/* -- ; write dirty buffers */
			saveb();
			break;
		case P_EMPTB:		/* -- ; discard buffers */
			emptyb();
			break;
		case P_FLUSH:		/* -- ; save then discard */
			saveb();
			emptyb();
			break;
		case P_LIST:		/* u -- */
			listbl(pop());
			break;
		case P_LOAD:		/* u -- */
			doload(pop());
			break;
		case P_ARROW:		/* -- ; continue with next block */
			doarrow();
			break;
		case P_SSTOP:		/* -- ; stop loading this block */
			store(a_toin, srclen);
			break;

		/* --- pictured numeric output (single-cell, unsigned) --- */
		case P_BRAKN:		/* <# : begin conversion */
			hp = HOLDT;
			break;
		case P_SHARP:		/* # : u -- u' ; hold one digit */
			ua = (unsigned)pop();
			b = fetch(a_base);
			if (b < 2 || b > 36) b = 10;
			n = ua % b;
			if (hp > HOLDB)
				mem[--hp] = n < 10 ? n + '0' : n - 10 + 'a';
			push((cell)(ua / b));
			break;
		case P_SHRPS:		/* #S : u -- 0 ; hold all digits */
			ua = (unsigned)pop();
			b = fetch(a_base);
			if (b < 2 || b > 36) b = 10;
			do {
				n = ua % b;
				if (hp > HOLDB)
					mem[--hp] = n<10 ? n+'0' : n-10+'a';
				ua = ua / b;
			} while (ua);
			push(0);
			break;
		case P_NBRAK:		/* #> : u -- addr len ; end conversion */
			pop();
			push(hp);
			push(HOLDT - hp);
			break;
		case P_HOLD:		/* c -- ; insert a char into the string */
			c = pop();
			if (hp > HOLDB) mem[--hp] = c;
			break;
		case P_SIGN:		/* n -- ; insert '-' if n negative */
			if (pop() < 0 && hp > HOLDB) mem[--hp] = '-';
			break;

		/* --- interpreter words --- */
		case P_PWORD:		/* WORD : char -- addr */
			push(parsew(pop() & 0xff));
			break;
		case P_FINDW:		/* FIND : -- cfa|0 (parse next word) */
			getword(nbuf);
			push(find(nbuf));	/* 0 if not found */
			break;
		case P_CONVT:		/* CONVERT : n addr -- n' addr' */
			a = pop();		/* addr of text-1 (79: first digit
						   is at addr+1) */
			b = pop();		/* running value */
			c = fetch(a_base);
			if (c < 2 || c > 36) c = 10;
			for (;;) {
				int d, ch;
				ch = mem[a + 1] & 0xff;
				if (ch >= '0' && ch <= '9') d = ch - '0';
				else if (ch >= 'a' && ch <= 'z') d = ch-'a'+10;
				else if (ch >= 'A' && ch <= 'Z') d = ch-'A'+10;
				else break;
				if (d >= c) break;
				b = b * c + d;
				a++;
			}
			push(b);
			push(a);
			break;
		case P_EXPCT:		/* EXPECT : addr n -- */
			n = pop(); a = pop();
			doexpect(a, n);
			break;
		case P_QUERY:		/* QUERY : -- ; refill terminal line */
			refill();
			break;

		case P_EMIT:
			emit(pop());
			break;
		case P_KEY:
			push(getchar());
			break;
		case P_CR:
			emit('\n');
			break;
		case P_SPACE:
			emit(' ');
			break;
		case P_SPCS:
			n = pop();
			while (n-- > 0) emit(' ');
			break;
		case P_DOT:
			printnum(pop());
			emit(' ');
			break;
		case P_UDOT:		/* U. : unsigned print */
			printu((unsigned)pop());
			emit(' ');
			break;
		case P_TYPE:
			n = pop(); a = pop();
			while (n-- > 0) emit(mem[a++] & 0xff);
			break;
		case P_DOTS:
			emit('<'); printnum(dsp); emit('>'); emit(' ');
			for (i = 0; i < dsp; i++) {
				printnum(dstack[i]); emit(' ');
			}
			break;
		case P_WORDS:
			dowords();
			break;
		case P_BYE:
			exit(0);

		/* --- (DO) runtime and LOOP runtime --- */
		case P_DODO:
			a = pop();		/* initial index */
			b = pop();		/* limit */
			rpush(b);
			rpush(a);
			break;
		case P_LOOP:
			n = 1;
			goto do_loop;
		case P_PLOOP:
			n = pop();
		do_loop:
			a = rpop();		/* index */
			b = rpop();		/* limit */
			a = a + n;
			/* directional exit: up-count stops at index>=limit,
			   down-count stops at index<limit */
			if ((n >= 0 && a >= b) || (n < 0 && a < b)) {
				ip += CELL;	/* done: skip back-branch */
			} else {
				rpush(b);
				rpush(a);
				ip = fetch(ip);	/* branch back */
			}
			break;
		case P_LEAVE:
			/* force exit at next LOOP by setting limit=index */
			if (rsp >= 2) rstack[rsp-2] = rstack[rsp-1];
			break;

		/* --- double-number (32-bit) words --- */
		case P_USTAR:		/* U* : u1 u2 -- ud (lo hi) */
			ua = (unsigned)pop() & 0xffff;
			ub = (unsigned)pop() & 0xffff;
			la = (long)ua * (long)ub;
			push((cell)(la & 0xffff));		/* low */
			push((cell)((la >> 16) & 0xffff));	/* high */
			break;
		case P_USLAS:		/* U/ : ud u -- urem uquot (U/MOD) */
			ua = (unsigned)pop() & 0xffff;		/* divisor */
			la = ((long)((unsigned)pop() & 0xffff)) << 16; /* high */
			la |= (long)((unsigned)pop() & 0xffff);	/* low */
			if (ua == 0) { push(0); push(0); }
			else {
				push((cell)(la % (long)ua));	/* rem */
				push((cell)(la / (long)ua));	/* quot */
			}
			break;
		case P_DPLUS:		/* D+ : d1 d2 -- d3 */
			la = dpop(); lb = dpop();
			dpush(lb + la);
			break;
		case P_DLT:		/* D< : d1 d2 -- flag */
			la = dpop(); lb = dpop();
			push(lb < la ? 1 : 0);
			break;
		case P_DNEG:		/* DNEGATE : d -- -d */
			la = dpop();
			dpush(-la);
			break;

		/* --- system --- */
		case P_ABORT:		/* clear stacks, back to top level */
			dsp = 0; rsp = 0;
			store(a_state, 0);
			if (jmpok) longjmp(toplevel, 1);
			return;
		case P_QUIT:		/* clear return stack, back to top level */
			rsp = 0;
			store(a_state, 0);
			if (jmpok) longjmp(toplevel, 1);
			return;
		case P_STD79:		/* 79-STANDARD : no-op assertion */
			break;

		default:
			/* immediate / defining words handled elsewhere */
			doextra(pr);
			break;
		}
	}
}

/* ------------------------------------------------------------------ */
/* words that touch the compiler / dictionary at run time             */

/* list the words visible from CONTEXT (its chain, then parents) */
dowords()
{
	int p, len, i, voc;
	voc = fetch(a_ctxt);
	while (voc) {
		p = fetch(voc);
		while (p) {
			len = mem[p+FBYTE] & LENM;
			for (i = 0; i < len; i++) emit(mem[p+NAMEOFF+i]);
			emit(' ');
			p = fetch(p);
		}
		voc = fetch(voc + CELL);	/* parent */
	}
	emit('\n');
}


/* create a header from the next input word; leave here at code field */
makeword()
{
	getword(nbuf);
	header(nbuf);
}

/* FORGET nbuf: discard the named word and everything defined after it.
   Reclaims dictionary space (here := word's link addr) and fixes every
   vocab head that pointed above the forget point.  Refuses to cross
   `fence` (which protects the built-in core). */
doforget()
{
	int xt, addr, voc, p;

	xt = find(nbuf);
	if (!xt) { outs(nbuf); outs(" ?\n"); return; }
	addr = wlink;			/* link-field address of the word */
	if (addr < fence) { errs("forget below fence\n"); return; }
	/* drop any vocabularies that were themselves defined at/after addr */
	while (voclist && voclist >= addr)
		voclist = fetch(voclist + 2*CELL);	/* its vlink */
	/* fix each surviving vocabulary head: drop entries at/after addr */
	voc = voclist;
	while (voc) {
		p = fetch(voc);
		while (p >= addr) p = fetch(p);
		store(voc, p);
		voc = fetch(voc + 2*CELL);
	}
	/* FORTH always survives (below fence); fix its head too */
	p = fetch(forthpf);
	while (p >= addr) p = fetch(p);
	store(forthpf, p);
	/* if CONTEXT/CURRENT pointed at a forgotten vocab, fall back */
	if (fetch(a_ctxt) >= addr) store(a_ctxt, forthpf);
	if (fetch(a_curr) >= addr) store(a_curr, forthpf);
	here = addr;			/* reclaim space */
	latest = fetch(fetch(a_curr));
}

doextra(pr)
int pr;
{
	int a, x, len, i;

	switch (pr) {

	case P_COLON:
		makeword();
		mem[latest+FBYTE] |= HIDN;	/* hide while compiling */
		comma(DOCOL);
		store(a_state, 1);
		break;
	case P_SEMI:
		comma(xt_exit);
		mem[latest+FBYTE] &= ~HIDN;
		store(a_state, 0);
		break;
	case P_CREAT:
		makeword();
		lastcf = here;	/* remember code-field addr for DOES> */
		comma(DOCRE);		/* 2-cell code field: [DOCRE][does] */
		comma(0);		/* does-slot, patched by DOES> */
		break;
	case P_VAR:
		makeword();
		comma(DOVAR);
		comma(0);
		break;
	case P_CON:
		x = pop();
		makeword();
		comma(DOCON);
		comma(x);
		break;
	case P_VOCAB:			/* VOCABULARY name */
		makeword();
		comma(DOVOC);
		x = here;		/* pfa: [head][parent][vlink] */
		comma(0);		/* head: empty */
		comma(forthpf);		/* parent: chains to FORTH */
		comma(voclist);		/* vlink: previous vocabulary */
		voclist = x;
		break;
	case P_DEFIN:			/* DEFINITIONS: CURRENT := CONTEXT */
		store(a_curr, fetch(a_ctxt));
		break;
	case P_FORGT:			/* FORGET name */
		getword(nbuf);
		doforget();
		break;
	case P_TICK:
		getword(nbuf);
		x = find(nbuf);
		if (x) push(x);
		else { outs(nbuf); outs(" ?\n"); }
		break;
	case P_IMMED:
		mem[latest+FBYTE] |= IMMED;
		break;
	case P_LBRAK:
		store(a_state, 0);
		break;
	case P_RBRAK:
		store(a_state, 1);
		break;
	case P_HEX:
		store(a_base, 16);
		break;
	case P_DEC:
		store(a_base, 10);
		break;

	case P_LITER:			/* LITERAL ( n -- ) immediate */
		x = pop();
		compile(xt_lit);
		comma(x);
		break;
	case P_DOES:			/* DOES> (immediate): compile (DOES>)
					   runtime; code after it is the child's */
		compile(xt_doesr);
		break;
	case P_BCOMP:			/* [COMPILE] name : compile even if immed */
		getword(nbuf);
		x = find(nbuf);
		if (x) compile(x);
		else { outs(nbuf); outs(" ?\n"); }
		break;
	case P_COMPL:			/* COMPILE name : postpone name's xt */
		getword(nbuf);
		x = find(nbuf);
		if (x) { compile(xt_lit); comma(x); compile(xt_comma); }
		else { outs(nbuf); outs(" ?\n"); }
		break;

	/* ---- control structures (all IMMEDIATE) ---- */
	case P_IF:
		compile(xt_zbran);
		push(here);
		comma(0);
		break;
	case P_ELSE:
		compile(xt_bran);
		x = here;		/* placeholder for the jump over else */
		comma(0);
		a = pop();		/* the IF placeholder */
		store(a, here);
		push(x);
		break;
	case P_THEN:
		a = pop();
		store(a, here);
		break;
	case P_BEGIN:
		push(here);
		break;
	case P_UNTIL:
		compile(xt_zbran);
		comma(pop());
		break;
	case P_AGAIN:
		compile(xt_bran);
		comma(pop());
		break;
	case P_WHILE:
		compile(xt_zbran);
		push(here);
		comma(0);
		break;
	case P_REPT:
		x = pop();		/* WHILE placeholder */
		a = pop();		/* BEGIN target */
		compile(xt_bran);
		comma(a);
		store(x, here);
		break;
	case P_DO:
		compile(xt_dodo);
		push(here);
		break;
	case P_CLOOP:
		compile(xt_loop);
		comma(pop());
		break;
	case P_CPLP:
		compile(xt_ploop);
		comma(pop());
		break;
	case P_PSTR:
		len = parsestr(nbuf);
		if (fetch(a_state)) {
			compile(xt_dotq);
			ccomma(len);
			for (i = 0; i < len; i++) ccomma(nbuf[i]);
			align();
		} else {
			for (i = 0; i < len; i++) emit(nbuf[i]);
		}
		break;

	case P_PAREN:
		tpos = fetch(a_toin);
		while (tpos < srclen && src[tpos] != ')') tpos++;
		if (tpos < srclen) tpos++;
		store(a_toin, tpos);
		break;
	case P_BSLSH:
		tpos = fetch(a_toin);
		if (fetch(a_blk)) {		/* in a block: to next line (64) */
			tpos = ((tpos / 64) + 1) * 64;
			if (tpos > srclen) tpos = srclen;
		} else {
			tpos = srclen;		/* terminal: rest of line */
		}
		store(a_toin, tpos);
		break;

	default:
		outs("?prim\n");
		break;
	}
}

/* ------------------------------------------------------------------ */
/* dialect warning: emit to stderr when `name` (just found, tag wdial)
   belongs to the OTHER exclusive dialect than the active one.  Lenient:
   the word still runs; we only warn.  Silent in native mode.        */
errs(s)
char *s;
{
	while (*s) { char b; b = *s++; write(2, &b, 1); }
}

warndial(name)
char *name;
{
	if (dialect == 0) return;		/* native superset: silent */
	if (wdial == D_COMMON || wdial == dialect) return;
	errs("warning: ");
	errs(name);
	errs(wdial == D_F77 ? " is Forth-77 (running -s 79)\n"
			    : " is Forth-79 (running -s 77)\n");
}

/* ------------------------------------------------------------------ */
/* the outer (text) interpreter -- process words from the current
   source (src/srclen/tpos) until it is exhausted, then return.  It does
   NOT refill; the caller (main loop for the terminal, doload for a
   block) manages the source. */

interp1()
{
	char word[64];
	int xt, n;
	cell v;

	for (;;) {
		n = getword(word);
		if (n == 0)
			return;			/* source exhausted */
		xt = find(word);
		if (xt) {
			warndial(word);
			if (fetch(a_state) && !(wflag & IMMED))
				compile(xt);
			else
				execute(xt);
		} else if (number(word, &v)) {
			if (fetch(a_state)) {
				compile(xt_lit);
				comma(v);
			} else {
				push(v);
			}
		} else {
			outs(word);
			outs(" ?\n");
			store(a_state, 0);
			dsp = 0;
		}
	}
}

/* LOAD ( u -- ): interpret block u as Forth source, then resume the
   previous source.  The buffer is locked so a nested LOAD cannot evict
   the text we are mid-parse in.  Save/restore is via C locals, so
   nesting works to NBUF deep. */
doload(u)
int u;
{
	char *ssrc;
	int slen, spos, sblk, sload, a;

	if (u < 0 || u >= MAXBLK) { errs("bad block\n"); return; }
	ssrc = src; slen = srclen; spos = fetch(a_toin);
	sblk = fetch(a_blk); sload = loadbuf;
	a = assign(u, 1);
	if (a < 0) return;			/* no free buffer */
	loadbuf = curbuf;
	bflock[loadbuf] = 1;
	src = &mem[a]; srclen = BLKSZ; store(a_toin, 0);
	store(a_blk, u);
	interp1();
	bflock[loadbuf] = 0;
	loadbuf = sload;
	src = ssrc; srclen = slen; store(a_toin, spos);
	store(a_blk, sblk);
}

/* --> ( -- ): stop this block, continue with the next.  Switches the
   current source in place, so it runs within the enclosing doload. */
doarrow()
{
	int u, a;
	if (fetch(a_blk) == 0) return;		/* only meaningful under LOAD */
	if (loadbuf >= 0) bflock[loadbuf] = 0;	/* release current block */
	u = fetch(a_blk) + 1;
	a = assign(u, 1);
	if (a < 0) { store(a_toin, srclen); return; }
	loadbuf = curbuf;
	bflock[loadbuf] = 1;
	src = &mem[a]; srclen = BLKSZ; store(a_toin, 0);
	store(a_blk, u);
}

/* ------------------------------------------------------------------ */
/* build the initial dictionary                                       */

int vari(name)			/* create a system VARIABLE, return its pfa */
char *name;
{
	int pfa;
	header(name);
	comma(DOVAR);
	pfa = here;
	comma(0);
	return pfa;
}

init()
{
	here = DICT0;		/* 0=sentinel, 2-4=EXEC scratch, 6-45=HOLD buf */
	latest = 0;
	curdial = D_COMMON;	/* core words below are dialect-neutral */

	a_state = vari("STATE");
	a_base  = vari("BASE");
	a_blk   = vari("BLK");
	a_scr   = vari("SCR");
	a_curr  = vari("CURRENT");
	a_ctxt  = vari("CONTEXT");
	a_toin  = vari(">IN");
	store(a_base, 10);
	store(a_state, 0);
	store(a_blk, 0);
	store(a_scr, 0);
	store(a_curr, 0);
	store(a_ctxt, 0);
	store(a_toin, 0);

	/* primitives whose xt we must remember for compiling */
	xt_lit   = prim("LIT", P_LIT);
	xt_exit  = prim("EXIT", P_EXIT);
	xt_bran  = prim("BRANCH", P_BRAN);
	xt_zbran = prim("0BRANCH", P_ZBRAN);
	xt_dotq  = prim("(.\")", P_DOTQ);
	xt_dodo  = prim("(DO)", P_DODO);
	xt_loop  = prim("(LOOP)", P_LOOP);
	xt_ploop = prim("(+LOOP)", P_PLOOP);

	prim("EXECUTE", P_EXEC);
	prim("DUP", P_DUP);
	prim("DROP", P_DROP);
	prim("SWAP", P_SWAP);
	prim("OVER", P_OVER);
	prim("ROT", P_ROT);
	prim("?DUP", P_QDUP);
	prim("DEPTH", P_DEPTH);
	prim(">R", P_TOR);
	prim("R>", P_FROMR);
	prim("R@", P_RAT);
	prim("I", P_I);
	prim("J", P_J);
	prim("LEAVE", P_LEAVE);

	prim("+", P_ADD);
	prim("-", P_SUB);
	prim("*", P_MUL);
	prim("/", P_DIV);
	prim("MOD", P_MOD);
	prim("/MOD", P_SLMOD);
	prim("*/", P_STARSL);
	dprim("NEGATE", P_NEG, D_F79);	/* 79 spelling */
	dprim("MINUS", P_NEG, D_F77);	/* 77 spelling */
	prim("ABS", P_ABS);
	prim("MIN", P_MIN);
	prim("MAX", P_MAX);
	prim("1+", P_1P);
	prim("1-", P_1M);
	prim("AND", P_AND);
	prim("OR", P_OR);
	prim("XOR", P_XOR);
	dprim("INVERT", P_INV, D_F79);	/* 79 spelling */
	dprim("COM", P_INV, D_F77);	/* 77 one's-complement */
	prim("=", P_EQ);
	prim("<", P_LT);
	prim(">", P_GT);
	prim("0=", P_ZEQ);
	/* NOT = 0= in BOTH 77 and 79 (Forth-79 Standard p.27: "identical
	   to 0="). No dialect conflict; COMMON word. One's-complement is
	   COM (77) / INVERT (79), handled separately above. */
	prim("NOT", P_ZEQ);
	prim("0<", P_ZLT);
	prim("0>", P_ZGT);

	prim("@", P_FETCH);
	prim("!", P_STORE);
	prim("+!", P_PSTOR);
	dprim("C@", P_CFET, D_F79);	/* 79 spelling */
	dprim("B@", P_CFET, D_F77);	/* 77 spelling */
	dprim("C!", P_CSTOR, D_F79);	/* 79 spelling */
	dprim("B!", P_CSTOR, D_F77);	/* 77 spelling */
	xt_comma = prim(",", P_COMMA);
	prim("C,", P_CCOMM);
	prim("HERE", P_HERE);
	prim("ALLOT", P_ALLOT);

	/* Forth-79 tier 1: single-cell primitives + wrappers */
	prim("2+", P_2P);
	prim("2-", P_2M);
	prim("U<", P_ULT);
	prim("*/MOD", P_SSMOD);
	prim("MOVE", P_MOVE);
	prim("CMOVE", P_CMOVE);
	prim("FILL", P_FILL);
	prim("COUNT", P_COUNT);
	prim("-TRAILING", P_DTRAI);
	prim("?", P_QUEST);
	prim("PICK", P_PICK);
	prim("ROLL", P_ROLL);
	prim("PAD", P_PAD);
	prim("COMPILE", P_COMPL);

	prim("EMIT", P_EMIT);
	prim("KEY", P_KEY);
	prim("CR", P_CR);
	prim("SPACE", P_SPACE);
	prim("SPACES", P_SPCS);
	prim(".", P_DOT);
	prim("U.", P_UDOT);
	prim("TYPE", P_TYPE);
	prim(".S", P_DOTS);
	prim("WORDS", P_WORDS);
	prim("BYE", P_BYE);

	/* Forth-79 tier 2: pictured numeric output + interpreter words */
	prim("<#", P_BRAKN);
	prim("#", P_SHARP);
	prim("#S", P_SHRPS);
	prim("#>", P_NBRAK);
	prim("HOLD", P_HOLD);
	prim("SIGN", P_SIGN);
	prim("WORD", P_PWORD);
	prim("FIND", P_FINDW);
	prim("CONVERT", P_CONVT);
	prim("EXPECT", P_EXPCT);
	prim("QUERY", P_QUERY);

	/* block / screen words */
	prim("BLOCK", P_BLOCK);
	prim("BUFFER", P_BUFFR);
	prim("UPDATE", P_UPDAT);
	prim("SAVE-BUFFERS", P_SAVEB);
	prim("EMPTY-BUFFERS", P_EMPTB);
	prim("FLUSH", P_FLUSH);
	prim("LIST", P_LIST);
	prim("LOAD", P_LOAD);
	prim(";S", P_SSTOP);

	prim(":", P_COLON);
	prim("CREATE", P_CREAT);
	prim("VARIABLE", P_VAR);
	prim("CONSTANT", P_CON);
	prim("'", P_TICK);
	prim("HEX", P_HEX);
	prim("DECIMAL", P_DEC);

	/* vocabulary words */
	prim("VOCABULARY", P_VOCAB);
	prim("DEFINITIONS", P_DEFIN);
	prim("FORGET", P_FORGT);

	/* Forth-79 tier 3: DOES>, double-number, system */
	xt_doesr = prim("(DOES>)", P_DORUN);	/* runtime, not user-called */
	prim("U*", P_USTAR);
	prim("U/", P_USLAS);
	prim("U/MOD", P_USLAS);
	prim("D+", P_DPLUS);
	prim("D<", P_DLT);
	prim("DNEGATE", P_DNEG);
	prim("ABORT", P_ABORT);
	prim("QUIT", P_QUIT);
	prim("79-STANDARD", P_STD79);

	/* immediate words */
	immprim(";", P_SEMI);
	immprim("IMMEDIATE", P_IMMED);
	immprim("[", P_LBRAK);
	immprim("]", P_RBRAK);
	immprim("IF", P_IF);
	immprim("ELSE", P_ELSE);
	immprim("THEN", P_THEN);
	immprim("BEGIN", P_BEGIN);
	diprim("UNTIL", P_UNTIL, D_F79);	/* 79 spelling */
	diprim("END", P_UNTIL, D_F77);	/* 77 spelling for UNTIL */
	immprim("AGAIN", P_AGAIN);
	immprim("WHILE", P_WHILE);
	immprim("REPEAT", P_REPT);
	immprim("DO", P_DO);
	immprim("LOOP", P_CLOOP);
	immprim("+LOOP", P_CPLP);
	immprim(".\"", P_PSTR);
	immprim("(", P_PAREN);
	immprim("\\", P_BSLSH);
	immprim("-->", P_ARROW);
	immprim("LITERAL", P_LITER);
	immprim("[COMPILE]", P_BCOMP);
	immprim("DOES>", P_DOES);

	/* Create the FORTH vocabulary word.  All core words above were linked
	   on the flat `latest` chain (current==0), so that chain IS FORTH's
	   contents.  Give FORTH a DOVOC word whose head = latest, parent = 0
	   (root), then point CONTEXT and CURRENT at it.  Everything defined
	   from now on links into FORTH via header(). */
	header("FORTH");
	comma(DOVOC);
	forthpf = here;
	comma(latest);		/* head: all core words so far */
	comma(0);		/* parent: none (root) */
	comma(0);		/* vlink: none */
	voclist  = forthpf;
	store(a_ctxt, forthpf);
	store(a_curr, forthpf);
	fence    = here;	/* FORGET may not cross into the core */
}

immprim(name, id)
char *name;
int id;
{
	prim(name, id);
	mem[latest+FBYTE] |= IMMED;
}

/* define a word tagged for a specific dialect (D_F77 / D_F79) */
dprim(name, id, dia)
char *name;
int id, dia;
{
	curdial = dia;
	prim(name, id);
	curdial = D_COMMON;
}

/* immediate variant */
diprim(name, id, dia)
char *name;
int id, dia;
{
	curdial = dia;
	prim(name, id);
	mem[latest+FBYTE] |= IMMED;
	curdial = D_COMMON;
}

main(argc, argv)
int argc;
char **argv;
{
	int i;
	char *bfile;

	dialect = 0;		/* native superset unless -s given */
	bfile = "blocks";	/* default blocks file */
	blkfd = -1;
	for (i = 1; i < argc; i++) {
		if (argv[i][0]=='-' && argv[i][1]=='s' && argv[i][2]==0
		    && i+1 < argc) {
			i++;
			if (argv[i][0]=='7' && argv[i][1]=='7') dialect = D_F77;
			else if (argv[i][0]=='7' && argv[i][1]=='9') dialect = D_F79;
			else { errs("usage: forth [-s 77|-s 79] [-b file]\n");
			       exit(1); }
		} else if (argv[i][0]=='-' && argv[i][1]=='b' && argv[i][2]==0
			   && i+1 < argc) {
			bfile = argv[++i];
		} else {
			errs("usage: forth [-s 77|-s 79] [-b file]\n");
			exit(1);
		}
	}
	init();
	openblk(bfile);
	outs("forth v7 -- ok\n");
	setjmp(toplevel);		/* ABORT/QUIT return here */
	jmpok = 1;
	while (refill()) {
		interp1();
		if (fetch(a_state) == 0) outs(" ok\n");
	}
	return 0;
}
