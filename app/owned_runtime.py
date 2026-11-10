"""Stop only the verified local process serving this Home's control socket."""
import ctypes
import os
from pathlib import Path
import platform
import select
import signal
import socket
import struct

from rpc import RPC


class StopError(Exception):
    def __init__(self,category):self.category=category;super().__init__(category)


def open_pidfd(pid):
    if hasattr(os,'pidfd_open'):return os.pidfd_open(pid,0)
    # Some Python builds omit the wrapper despite a supporting Linux kernel.
    # Keep the same kernel handle semantics; never fall back to os.kill(pid).
    if platform.system()!='Linux' or platform.machine() not in ('x86_64','aarch64'):
        raise StopError('unverified_runtime')
    libc=ctypes.CDLL(None,use_errno=True);libc.syscall.restype=ctypes.c_long
    result=libc.syscall(ctypes.c_long(434),ctypes.c_int(pid),ctypes.c_uint(0))
    if result<0:
        error=ctypes.get_errno();raise OSError(error,os.strerror(error))
    return result


def identity(client,binary):
    # The kernel identifies the peer of our actual initialized RPC connection.
    # No PID files, process-name matching, or browser-provided process IDs.
    pid,uid,_=struct.unpack('3i',client.ws.socket.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,struct.calcsize('3i')))
    process=Path('/proc')/str(pid)
    if pid<=1 or pid==os.getpid() or uid!=os.getuid() or process.stat().st_uid!=uid:
        raise StopError('unverified_runtime')
    if (process/'exe').resolve(strict=True)!=Path(binary).resolve(strict=True):
        raise StopError('unverified_runtime')
    args=(process/'cmdline').read_bytes().split(b'\0')
    try:position=args.index(b'app-server')
    except ValueError:raise StopError('unverified_runtime') from None
    if any(word in args[position+1:] for word in (b'proxy',b'daemon')):
        raise StopError('unverified_runtime')
    # starttime, after the parenthesized command field (which can contain spaces).
    started=(process/'stat').read_text().rsplit(')',1)[1].split()[19]
    return pid,started


def stop(home,binary,*,rpc_factory=None,timeout=35):
    client=None;pidfd=None
    path=str(Path(home)/'app-server-control/app-server-control.sock')
    try:
        client=(rpc_factory or RPC)(socket=path)
        pid,started=identity(client,binary)
        pidfd=open_pidfd(pid)
        if identity(client,binary)!=(pid,started):raise StopError('unverified_runtime')
        # Prove the original peer is still alive after opening the pidfd, so a
        # reused numeric PID cannot redirect the signal to a different process.
        client.call('account/read',{'refreshToken':False})
        client.close();client=None
        try:signal.pidfd_send_signal(pidfd,signal.SIGTERM)
        except ProcessLookupError:pass
        if not select.select([pidfd],[],[],timeout)[0]:raise StopError('daemon_timeout')
        # Do not change credentials if a desktop client has already started a
        # replacement. Never chase or stop that new process automatically.
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as probe:
            probe.settimeout(1)
            try:probe.connect(path)
            except (FileNotFoundError,ConnectionRefusedError):pass
            else:raise StopError('runtime_replaced')
    except StopError:raise
    except Exception:raise StopError('unverified_runtime') from None
    finally:
        if client:
            try:client.close()
            except Exception:pass
        if pidfd is not None:os.close(pidfd)
