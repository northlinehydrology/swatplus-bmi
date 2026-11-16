/* bmi_fork.c: the few operating system calls the exchange mode needs.
 *
 * Branching duplicates a paused SWAT+ process with fork(), so every copy
 * continues from the same state. Fortran has no portable way to fork,
 * change directory or rewire standard streams, so these are in C.
 *
 * Arrays and replies cross the exchange as raw bytes, read and written
 * here whole: gfortran writes a formatted record to a pipe with a system
 * call per value, and reads an unformatted stream from a pipe in a single
 * read call, taking a short one for the end of the file.
 *
 * On Windows there is no fork (branch is refused, and the wrapper branches
 * by saving and loading instead); the other calls are the C runtime's own,
 * with the descriptors set to binary so no byte is translated.
 */

#include <errno.h>
#include <fcntl.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>

#if defined(_WIN32)
#include <direct.h>
#include <io.h>
#include <process.h>
#else
#include <signal.h>
#include <sys/types.h>
#include <unistd.h>
#endif

#if defined(_WIN32)
/* The C runtime's read and write take an unsigned count, so a long array
 * goes in pieces. */
#define SWX_CHUNK ((size_t)1 << 30)
#define swx_sys_read(fd, p, n) _read((fd), (p), (unsigned)((n) < SWX_CHUNK ? (n) : SWX_CHUNK))
#define swx_sys_write(fd, p, n) _write((fd), (p), (unsigned)((n) < SWX_CHUNK ? (n) : SWX_CHUNK))
#else
#define swx_sys_read(fd, p, n) read((fd), (p), (n))
#define swx_sys_write(fd, p, n) write((fd), (p), (n))
#endif

/* Fork the process. Children are reaped by the system (SIGCHLD ignored),
 * so a branch that ends before its parent leaves no zombie. Returns the
 * child's pid in the parent, 0 in the child, -1 on failure (always, on
 * Windows). */
int swx_fork(void)
{
#if defined(_WIN32)
    return -1;
#else
    signal(SIGCHLD, SIG_IGN);
    return (int)fork();
#endif
}

/* Change the working directory. Returns 0 on success. */
int swx_chdir(const char *path)
{
#if defined(_WIN32)
    return _chdir(path);
#else
    return chdir(path);
#endif
}

/* In a new branch: let go of the parent's standard input and output (so the
 * parent's caller sees end of file when the parent ends) and send standard
 * error to err_path. Returns 0 on success. */
int swx_detach(const char *err_path)
{
#if defined(_WIN32)
    (void)err_path;
    return -1;
#else
    int nul = open("/dev/null", O_RDWR);
    int err;

    if (nul < 0)
        return -1;
    dup2(nul, 0);
    dup2(nul, 1);
    close(nul);
    err = open(err_path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (err >= 0) {
        dup2(err, 2);
        close(err);
    }
    return 0;
#endif
}

int swx_getpid(void)
{
#if defined(_WIN32)
    return (int)_getpid();
#else
    return (int)getpid();
#endif
}

/* A second descriptor for standard input or output, for the arrays and the
 * replies; binary on Windows, where standard streams start as text. */
int swx_dup(int fd)
{
#if defined(_WIN32)
    int d = _dup(fd);
    if (d >= 0)
        _setmode(d, _O_BINARY);
    return d;
#else
    return dup(fd);
#endif
}

/* Open a path (a branch's named pipe) for reading, or for writing when
 * for_write is nonzero. Returns the descriptor, or -1. */
int swx_open(const char *path, int for_write)
{
#if defined(_WIN32)
    return _open(path, (for_write ? _O_WRONLY : _O_RDONLY) | _O_BINARY);
#else
    return open(path, for_write ? O_WRONLY : O_RDONLY);
#endif
}

void swx_close(int fd)
{
    if (fd < 0)
        return;
#if defined(_WIN32)
    _close(fd);
#else
    close(fd);
#endif
}

/* Read exactly n bytes, however the pipe delivers them. Returns 0, or -1
 * if the other end closed or the read failed. */
int swx_read(int fd, void *buf, size_t n)
{
    char *p = buf;

    while (n > 0) {
        long k = (long)swx_sys_read(fd, p, n);
        if (k < 0 && errno == EINTR)
            continue;
        if (k <= 0)
            return -1;
        p += k;
        n -= (size_t)k;
    }
    return 0;
}

/* Write exactly n bytes. Returns 0, or -1 if the write failed. */
int swx_write(int fd, const void *buf, size_t n)
{
    const char *p = buf;

    while (n > 0) {
        long k = (long)swx_sys_write(fd, p, n);
        if (k < 0 && errno == EINTR)
            continue;
        if (k <= 0)
            return -1;
        p += k;
        n -= (size_t)k;
    }
    return 0;
}

/* Write a reply: the n characters of line and a newline, in one write, so
 * a reply never arrives in pieces. Returns 0, or -1 if the write failed. */
int swx_write_line(int fd, const char *line, size_t n)
{
    char small[512];
    char *buf = small;
    int r;

    if (n + 1 > sizeof small) {
        buf = (char *)malloc(n + 1);
        if (buf == NULL)
            return -1;
    }
    memcpy(buf, line, n);
    buf[n] = '\n';
    r = swx_write(fd, buf, n + 1);
    if (buf != small)
        free(buf);
    return r;
}
