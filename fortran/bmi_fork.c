/* bmi_fork.c: the few POSIX calls the exchange mode's branch command needs.
 *
 * Branching duplicates a paused SWAT+ process with fork(), so every copy
 * continues from the same state. Fortran has no portable way to fork,
 * change directory or rewire standard streams, so these are in C. Mac and
 * Linux only; the Windows build leaves this file out and refuses branch.
 */

#include <fcntl.h>
#include <signal.h>
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
