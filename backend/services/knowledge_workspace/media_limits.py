"""Exec only local media decoders with OS bounds, without threaded preexec_fn."""
import os
import sys


def main():
    if len(sys.argv)<2 or sys.argv[1] not in {'ffmpeg','ffprobe','pdftoppm'}:
        raise SystemExit(2)
    if os.name=='posix':
        import resource
        resource.setrlimit(resource.RLIMIT_CPU,(45,45))
        resource.setrlimit(resource.RLIMIT_FSIZE,(32*1024*1024,32*1024*1024))
        resource.setrlimit(resource.RLIMIT_NOFILE,(128,128))
        if sys.platform.startswith('linux'):
            resource.setrlimit(resource.RLIMIT_AS,(1024*1024*1024,1024*1024*1024))
    os.execvp(sys.argv[1],sys.argv[1:])


if __name__=='__main__':main()
