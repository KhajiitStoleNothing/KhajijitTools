import os
import socket
import sys
import argparse
import multiprocessing
import itertools
import time
from functools import partial

DEFAULT_TIMEOUT_INIT = 25  # '--timeout-init'
DEFAULT_TIMEOUT_ENUM = 10  # '--timeout-enum'

DEFAULT_RETRY_INIT = 4  # '--retry-init'
DEFAULT_RETRY_ENUM = 5  # '--retry-enum'

DEFAULT_RECONNECT = 3  # '--reconnect'
DEFAULT_THREADS = 5 # '--threads' 
DEFAULT_MODE = "VRFY"
SUPPORTED_MODES = ["VRFY", "EXPN", "RCPT"]

DEFAULT_MAIL_FROM = "user@example.com"


def connect(host, port):
    """Connect to remote host."""
    # Create socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    except socket.error as msg:
        return (None, msg)
    # Get remote IP
    try:
        addr = socket.gethostbyname(host)
    except socket.gaierror as msg:
        s.close()
        return (None, msg)
    # Connect
    try:
        s.connect((addr, port))
    except socket.error as msg:
        s.close()
        return (None, msg)

    return (s, None)


def send(s, data):
    """Send data to socket."""
    try:
        data += "\r\n"
        s.send(str2b(data))
    except socket.error as msg:
        return (False, msg)

    return (True, None)


def receive(s, timeout, bufsize=1024):
    """Read one newline terminated line from a connected socket."""
    data = ""
    size = len(data)
    s.settimeout(timeout)

    while True:
        try:
            data += b2str(s.recv(bufsize))
        except socket.error as err:
            return (False, err)
        if not data:
            return (False, "upstream connection is gone while receiving")
        # Newline terminates the read request
        if data.endswith("\n"):
            break
        if data.endswith("\r"):
            break
        # Sometimes a newline is missing at the end
        # If this round has the same data length as previous, we're done
        if size == len(data):
            break
        size = len(data)
    # Remove trailing newlines
    data = data.rstrip("\r\n")
    data = data.rstrip("\n")
    data = data.rstrip("\r")
    return (True, data)

def b2str(data):
    """Convert bytes into string type."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("ascii")
    except UnicodeDecodeError:
        return data.decode("latin-1")

def str2b(data):
    """Convert string into byte type."""
    try:
        return data.encode("latin1")
    except UnicodeDecodeError:
        return data

def _args_check_port(value):
    """Check argument for valid port number."""
    min_port = 1
    max_port = 65535

    try:
        intvalue = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError('"%s" is an invalid port number.' % value)

    if intvalue < min_port or intvalue > max_port:
        raise argparse.ArgumentTypeError('"%s" is an invalid port number.' % value)
    return intvalue


def _args_check_mode(value):
    """Check argument for valid mode."""
    strval = value
    if strval not in SUPPORTED_MODES:
        raise argparse.ArgumentTypeError(
            'Invalid mode "%s". Supported: %s' % (value, ", ".join(SUPPORTED_MODES))
        )
    return strval


def _args_check_file(value):
    """Check argument for existing file."""
    strval = value
    if not os.path.isfile(strval):
        raise argparse.ArgumentTypeError('File "%s" not found.' % value)
    return strval

def output(data, verbose):
    """print data if verbose is enabled."""
    if verbose:
        print(data)

def enum_users(user,mode, domain, wrap, reconnect, retry, timeout, verbose, conn):
    """Enumerate users on SMTP server."""
    s = init_connection(
                    conn["host"],
                    conn["port"],
                    mode,
                    conn["from"],
                    conn["retry"],
                    conn["timeout"],
                    verbose,
                )
    failure = False
    message = ""

    if not verbose:
        print("\033[93m[TEST] {} ...\033[00m".format(user), end="\r")
        sys.stdout.flush()

        # Reconnect
    for j in range(1, reconnect + 1):
        failure = False
        for i in range(1, retry + 1):

            # Configure user/mail with/without '<'/'>' wrapped
            tmp_user = user
            if domain is not None:
                tmp_user += "@" + domain
            if wrap:
                tmp_user = "<" + tmp_user + ">"            
            if mode == "RCPT":
                command = "RCPT TO:" + tmp_user
            else:
                command = mode + " " + tmp_user            
            output(
                    "[Reconn {}/{}] [Retry {}/{}] Testing: {} ...".format(
                        j, reconnect, i, retry, command
                    ),
                    verbose,
            )
            succ, err = send(s, command)
            if succ:
                break
        if not succ:
            s.close()
            failure = True
            message = err
            s = init_connection(
                conn["host"],
                conn["port"],
                mode,
                conn["from"],
                conn["retry"],
                conn["timeout"],
                verbose,
            )
            continue        # Wait for answer with retry
        for i in range(1, retry + 1):
            output(
                "[Reconn {}/{}] [Retry {}/{}] Waiting for answer ...".format(
                    j, reconnect, i, retry
                ),
                verbose,
            )
            succ, reply = receive(s, timeout)
            if succ:
                break
        if not succ:
            s.close()
            failure = True
            message = reply
            s = init_connection(
                conn["host"],
                conn["port"],
                mode,
                conn["from"],
                conn["retry"],
                conn["timeout"],
                verbose,
            )
            continue        
        if not failure:
            break    
        if failure:
            s.close()
            print(message, file=sys.stderr)
            sys.exit(1)    
    if reply.startswith("250 "):
        print("\033[92m[SUCC] {}{}\033[00m{}".format(user, " " * 50, reply))
    else:
        print("\033[91m[----] {}{}\033[00m{}".format(user, " " * 50, reply))

def init_connection(host, port, mode, from_mail, retry, timeout, verbose):
    """Initialize SMTP connection."""
    # Connect with retry
    for i in range(1, retry + 1):
        output("[{}/{}] Connecting to {}:{} ...".format(i, retry, host, port), verbose)
        s, err = connect(host, port)
        if s is not None:
            break
    if s is None:
        print(err, file=sys.stderr)
        sys.exit(1)

    # Receive banner with retry
    for i in range(1, retry + 1):
        output("[{}/{}] Waiting for banner ...".format(i, retry), verbose)
        succ, banner = receive(s, timeout)
        if succ:
            break
    if not succ:
        s.close()
        print(banner, file=sys.stderr)
        sys.exit(1)
    print("%s" % (banner))

    # Send greeting with retry
    for i in range(1, retry + 1):
        command = "HELO test"
        output("[{}/{}] Sending greeting: {}".format(i, retry, command), verbose)
        succ, err = send(s, command)
        if succ:
            break
    if not succ:
        s.close()
        print(err, file=sys.stderr)
        sys.exit(1)

    # Waiting for greeting with rety
    for i in range(1, retry + 1):
        output("[{}/{}] Waiting for greeting reply ...".format(i, retry), verbose)
        succ, greeting = receive(s, timeout)
        if succ:
            break
    if not succ:
        s.close()
        print(greeting, file=sys.stderr)
        sys.exit(1)
    print("%s" % (greeting))

    # In RCPT mode, we need to ensure to issue a
    # MAIL FROM: <user>[@<domain>] first and verify a successful anwer
    # as such: 250 2.1.0 <user>[@domain>]... Sender OK
    if mode == "RCPT":
        # Send MAIL FROM:
        for i in range(1, retry + 1):
            command = "MAIL FROM: " + from_mail
            output("[{}/{}] Sending: {}".format(i, retry, command), verbose)
            succ, err = send(s, command)
            if succ:
                break
        # Waiting for answer
        for i in range(1, retry + 1):
            output("[{}/{}] Waiting for MAIL FROM reply ...".format(i, retry), verbose)
            succ, reply = receive(s, timeout)
            if succ and not reply.startswith("250"):
                output(reply, verbose)
            if succ and reply.startswith("250"):
                break
        if not succ or not reply.startswith("250"):
            s.close()
            print(reply, file=sys.stderr)
            sys.exit(1)
        print("%s" % (reply))

    return s

def get_names_from_wordlist(filepath):
    """Read wordlist line by line and store each line as a list entry."""
    with open(filepath) as f:
        content = f.readlines()
    # Remove whitespace characters like '\n' at the end of each line
    return [x.strip() for x in content]


def get_args():
    """Retrieve command line arguments."""
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawTextHelpFormatter,
        usage="""%(prog)s [options] -u/-U host port
       %(prog)s --help
       %(prog)s --version
""",
        description="SMTP user enumeration tool with clever timeout, retry and reconnect"
        + " functionality."
        + """

Some SMTP server take a long time for initial communication (banner and greeting) and then
handle subsequent commands quite fast. Then again they randomly start to get slow again.

This implementation of SMTP user enumeration counteracts with granular timeout, retry and
reconnect options for initial communication and enumeration separately.
The defaults should work fine, however if you encounter slow enumeration, adjust the settings
according to your needs.

Additionally if it encounters anything like '421 Too many errors on this connection' it will
automatically and transparently reconnect and continue from where it left off.
""",
    )
    parser.add_argument(
        "-m",
        "--mode",
        metavar="mode",
        required=False,
        type=_args_check_mode,
        default=DEFAULT_MODE,
        help="Mode to enumerate SMTP users.\nSupported modes: "
        + ", ".join(SUPPORTED_MODES)
        + "\nDefault: "
        + DEFAULT_MODE,
    )
    parser.add_argument(
        "-d",
        "--domain",
        metavar="addr",
        required=False,
        type=str,
        help="""Domain to append to users to convert into email addresses.
Useful if you see this response: '550 A valid address is required'
Default: Nothing appended""",
    )
    parser.add_argument(
        "-w",
        "--wrap",
        action="store_true",
        required=False,
        default=False,
        help="""Wrap the username or email address in '<' and '>' characters.
Usefule if you see this response: '501 5.5.2 Syntax error in parameters or arguments'.
Makes sense to combine with -d/--domain option.
Default: Nothing wrapped""",
    )
    parser.add_argument(
        "-f",
        "--from-mail",
        metavar="addr",
        required=False,
        default=DEFAULT_MAIL_FROM,
        type=str,
        help="MAIL FROM email address. Only used in RCPT mode" + "\nDefault: " + DEFAULT_MAIL_FROM,
    )
    parser.add_argument(
        "-F",
        "--firstnames",
        metavar="firstnames",
        required=True,
        type=_args_check_file,
        help="Newline separated wordlist of users to test.",
    )
    parser.add_argument(
        "-S",
        "--surnames",
        metavar="surnames",
        required=True,
        type=_args_check_file,
        help="Newline separated wordlist of users to test.",
    )
    parser.add_argument(
        "-V",
        "--verbose",
        action="store_true",
        required=False,
        default=False,
        help="Show verbose output. Useful to adjust your timing and retry settings.",
    )
    parser.add_argument(
        "--timeout-init",
        metavar="sec",
        required=False,
        default=DEFAULT_TIMEOUT_INIT,
        type=int,
        help="""Timeout for initial communication (connect, banner and greeting).
Default: """
        + str(DEFAULT_TIMEOUT_INIT),
    )
    parser.add_argument(
        "--threads",
        metavar="threads",
        required=False,
        default=DEFAULT_THREADS,
        type=int,
        help="""Timeout for initial communication (connect, banner and greeting).
Default: """
        + str(DEFAULT_THREADS),
    )
    parser.add_argument(
        "--timeout-enum",
        metavar="sec",
        required=False,
        default=DEFAULT_TIMEOUT_ENUM,
        type=int,
        help="""Timeout for user enumeration.
Default: """
        + str(DEFAULT_TIMEOUT_ENUM),
    )
    parser.add_argument(
        "--retry-init",
        metavar="int",
        required=False,
        default=DEFAULT_RETRY_INIT,
        type=int,
        help="""Number of retries for initial communication (connect, banner and greeting).
Default: """
        + str(DEFAULT_RETRY_INIT),
    )
    parser.add_argument(
        "--retry-enum",
        metavar="int",
        required=False,
        default=DEFAULT_RETRY_ENUM,
        type=int,
        help="""Number of retries for user enumeration.
Default: """
        + str(DEFAULT_RETRY_ENUM),
    )
    parser.add_argument(
        "--reconnect",
        metavar="int",
        required=False,
        default=DEFAULT_RECONNECT,
        type=int,
        help="""Number of reconnects during user enumeration after retries have exceeded.
Default: """
        + str(DEFAULT_RECONNECT),
    )
    parser.add_argument("host", type=str, help="IP or hostname to connect to.")
    parser.add_argument("port", type=_args_check_port, help="Port to connect to.")
    return parser.parse_args()


def main():
    """Start the program."""
    args = get_args()
    surnames = get_names_from_wordlist(args.surnames)
    firstnames = get_names_from_wordlist(args.firstnames)
    users = [ "".join(list(x)) for x in itertools.product(firstnames,surnames)]

    print("Start enumerating users with %s mode ..." % args.mode)
    conn = {
            "host": args.host,
            "port": args.port,
            "from": args.from_mail,
            "retry": args.retry_init,
            "timeout": args.timeout_init,
    }
    
    pool = multiprocessing.Pool(processes=args.threads)
    pool_outputs = pool.map(partial(enum_users, mode=args.mode, domain=args.domain, wrap=args.wrap, reconnect=args.reconnect, retry=args.retry_enum, timeout=args.timeout_enum, verbose=args.verbose, conn=conn), users)

    pool.close()
    pool.join()


if __name__ == "__main__":
    # Catch Ctrl+c and exit without error message
    try:
        main()
    except KeyboardInterrupt:
        print()
        sys.exit(1)



