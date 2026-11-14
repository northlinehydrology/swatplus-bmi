/* bmi_fork.c: the few POSIX calls the exchange mode needs.
 *
 * Branching duplicates a paused SWAT+ process with fork(), so every copy
 * continues from the same state. Fortran has no portable way to fork,
 * change directory or rewire standard streams, so these are in C.
 *
 * Arrays cross the exchange as raw bytes, read and written here whole:
 * gfortran writes a formatted record to a pipe with a system call per
 * value, and reads an unformatted stream from a pipe in a single read
 * call, taking a short one for the end of the file.
 *
 * Mac and Linux only; the Windows build leaves this file out, and exchange
 * mode stops there at startup.
 */

#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stddef.h>
#include <sys/types.h>
#include <unistd.h>

/* Fork the process. Children are reaped by the system (SIGCHLD ignored),
 * so a branch that ends before its parent leaves no zombie. Returns the
 * child's pid in the parent, 0 in the child, -1 on failure. */
int swx_fork(void)
{
    signal(SIGCHLD, SIG_IGN);
    return (int)fork();
}

/* Change the working directory. Returns 0 on success. */
int swx_chdir(const char *path)
{
    return chdir(path);
}

/* In a new branch: let go of the parent's standard input and output (so the
 * parent's caller sees end of file when the parent ends) and send standard
 * error to err_path. Returns 0 on success. */
int swx_detach(const char *err_path)
{
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
}

int swx_getpid(void)
{
    return (int)getpid();
}

/* A second descriptor for standard input or output, for the arrays. */
int swx_dup(int fd)
{
    return dup(fd);
}

/* Open a path (a branch's named pipe) for reading, or for writing when
 * for_write is nonzero. Returns the descriptor, or -1. */
int swx_open(const char *path, int for_write)
{
    return open(path, for_write ? O_WRONLY : O_RDONLY);
}

void swx_close(int fd)
{
    if (fd >= 0)
        close(fd);
}

/* Read exactly n bytes, however the pipe delivers them. Returns 0, or -1
 * if the other end closed or the read failed. */
int swx_read(int fd, void *buf, size_t n)
{
    char *p = buf;

    while (n > 0) {
        ssize_t k = read(fd, p, n);
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
        ssize_t k = write(fd, p, n);
        if (k < 0 && errno == EINTR)
            continue;
        if (k <= 0)
            return -1;
        p += k;
        n -= (size_t)k;
    }
    return 0;
}
