#!/usr/bin/python

# pyxbackup - Robust Xtrabackup based MySQL Backups Manager
#
# @author Jervin Real <jervin.real@percona.com>

import sys, traceback, os, errno, signal
import time, calendar, shutil, re, pwd
import smtplib, MySQLdb, base64
from datetime import datetime, timedelta
from ConfigParser import ConfigParser, NoOptionError
from optparse import OptionParser
from subprocess import Popen, PIPE, STDOUT, CalledProcessError
from struct import unpack

XB_BIN_NAME = 'pyxbackup'

xb_opt_config = None
xb_opt_config_section = None
xb_opt_stor_dir = ''
xb_opt_work_dir = ''
xb_opt_mysql_user = None
xb_opt_mysql_pass = None
xb_opt_mysql_host = 'localhost'
xb_opt_mysql_port = 3306
xb_opt_mysql_sock = '/tmp/mysql.sock'
xb_opt_mysql_cnf = None
xb_opt_retention_binlogs = False
xb_opt_compress = False
xb_opt_compress_with = 'gzip'
xb_opt_apply_log = False
xb_opt_prepare_memory = 128
xb_opt_retention_sets = 2
xb_opt_retention_months = 0
xb_opt_retention_weeks = 0
xb_opt_debug = False
xb_opt_quiet = False
xb_opt_status_format = None
xb_opt_command = 'status'
xb_opt_restore_backup = None
xb_opt_restore_dir = None
xb_opt_remote_stor_dir = None
xb_opt_remote_host = None
xb_opt_remote_push_only = None
xb_opt_remote_script = XB_BIN_NAME
xb_opt_remote_nc_port = 0
xb_opt_remote_nc_port_min = 0
xb_opt_remote_nc_port_max = 0
xb_opt_ssh_opts = ''
xb_opt_ssh_user = None
xb_opt_notify_by_email = None
xb_opt_notify_on_success = None
xb_opt_meta_item = None
xb_opt_wipeout = False
xb_opt_first_binlog = False
xb_opt_binlog_binary = None
xb_opt_binlog_from_master = False
xb_opt_encrypt = False
xb_opt_encrypt_key_file = None
xb_opt_extra_ibx_options = None
xb_opt_purge_bitmaps = None

xb_hostname = None
xb_user = None
xb_stor_full = None
xb_stor_incr = None
xb_stor_weekly = None
xb_stor_monthly = None
xb_stor_binlogs = None

xb_curdate = None
xb_cfg = None
xb_cwd = None
xb_version = 0.4
xb_ibx_opts = ''
xb_ibx_bin = 'innobackupex'
xb_zip_bin = 'gzip'
xb_xbs_bin = 'xbstream'
xb_this_backup = None
xb_this_backup_remote = None
xb_this_binlog = None
xb_this_master_binlog = None
xb_this_last_lsn = None
xb_last_full = None
xb_last_incr = None
xb_full_list = None
xb_incr_list = None
xb_weekly_list = None
xb_monthly_list = None
xb_last_backup = None
xb_last_backup_is = None
xb_first_binlog = None
xb_last_binlog = None
xb_binlogs_list = None
xb_binlog_name = None
xb_exit_code = 0
xb_prepared_backup = ''
xb_backup_is_success = False
xb_prepare_is_success = False
xb_backup_in_progress = None
xb_info_bkp_start = None
xb_info_bkp_end = None
xb_info_prep_start = None
xb_info_prep_end = None
xb_log_file = ''
xb_log_fd = None
xb_is_last_day_of_week = False
xb_is_last_day_of_month = False
xb_mysqldb = None
xb_backup_summary = None

XB_CMD_INCR = 'incr'
XB_CMD_FULL = 'full'
XB_CMD_LIST = 'list'
XB_CMD_STAT = 'status'
XB_CMD_PREP = 'restore-set'
XB_CMD_APPL = 'apply-last'
XB_CMD_PRUNE = 'prune'
XB_CMD_META = 'meta'
XB_CMD_BINLOGS = 'binlog-stream'
XB_CMD_WIPE = 'wipeout'
XB_TAG_FILE = 'xtrabackup_checkpoints'
XB_CKP_FILE = 'xtrabackup_checkpoints'
XB_LOG_FILE = 'xtrabackup_logfile'
XB_LCK_FILE = ''
XB_META_FILE = 'backup.meta'
XB_BKP_LOG = 'innobackupex-backup.log'
XB_APPLY_LOG = 'innobackupex-prepare.log'
XB_LOG_NAME = XB_BIN_NAME + '.log'
XB_SSH_TMPFILE = '/tmp/' + XB_BIN_NAME + '-ssh-result'
XB_SIGTERM_CAUGHT = False
XB_VERSION_MAJOR = 0
XB_VERSION_MINOR = 0
XB_VERSION_REV = 0
XB_VERSION = None

XB_EXIT_COMPRESS_FAIL       = 1
XB_EXIT_REMOTE_PUSH_FAIL    = 2
XB_EXIT_EXTRACT_FAIL        = 4
XB_EXIT_BITMAP_PURGE_FAIL   = 8
XB_EXIT_NO_FULL             = 16
XB_EXIT_DECRYPT_FAIL        = 32
XB_EXIT_APPLY_FAIL          = 64
XB_EXIT_INNOBACKUP_FAIL     = 65
XB_EXIT_BINLOG_STREAM_FAIL  = 66
XB_EXIT_REMOTE_CMD_FAIL     = 96
XB_EXIT_BY_DEATH            = 128
XB_EXIT_EXCEPTION           = 255

# What commands does not need a log or lock file
cmd_no_log = [
    XB_CMD_LIST, XB_CMD_STAT, XB_CMD_META, XB_CMD_BINLOGS, 
    XB_CMD_PRUNE]
cmd_no_lock = cmd_no_log
cmd_backups = [XB_CMD_FULL, XB_CMD_INCR]

def date(unixtime, format = '%m/%d/%Y %H:%M:%S'):
    d = datetime.fromtimestamp(unixtime)
    return d.strftime(format)

def _xb_version(verstr = None, tof = False):
    global XB_VERSION_MAJOR
    global XB_VERSION_MINOR
    global XB_VERSION_REV
    global XB_VERSION
    global xb_ibx_bin

    if verstr is None:
        if XB_VERSION is not None:
            if tof: return float("%d.%d" % (XB_VERSION_MAJOR, XB_VERSION_MINOR))
            else: return True

        p = Popen(["xtrabackup", "--version"], stdout=PIPE, stderr=PIPE)
        
        # weird, xtrabackup outputs version 
        # string on STDERR instead of STDOUT
        out, err = p.communicate()
        ver = re.search('version ([\d\.]+)', err)
        major, minor, rev = ver.group(1).split('.')
    
        XB_VERSION_MAJOR = int(major) if major else 0
        XB_VERSION_MINOR = int(minor) if minor else 0
        XB_VERSION_REV = int(rev) if rev else 0
        XB_VERSION = "%d.%d.%d" % (
            XB_VERSION_MAJOR, XB_VERSION_MINOR, XB_VERSION_REV)

        if XB_VERSION_MAJOR == 0:
            _error(
                "Invalid xtrabackup version or unable to determine valid " 
                "version string or binary version non-GA release")
            _error("Version string was \"%s\"" % err)
            _die("Exiting")

        if XB_VERSION_MINOR >= 3: xb_ibx_bin = 'xtrabackup'

        _debug("Found xtrabackup version %d.%d.%d" % (
            XB_VERSION_MAJOR, XB_VERSION_MINOR, XB_VERSION_REV))
    else:
        major, minor, rev = verstr.split('.')
        major = int(major) if major else 0
        minor = int(minor) if minor else 0
        rev = int(rev) if rev else 0

        if tof: 
            return float("%d.%d" % (major, minor))
        else: return [major, minor, rev]

    return True

def _out(tag, *msgs):
    s = ''

    if not msgs:
        return

    for msg in msgs:
        s += str(msg)

    out = "[%s] %s: %s" % (date(time.time()), tag, s)

    if xb_log_fd is not None:
        os.write(xb_log_fd, "%s\n" % out)

    if not xb_opt_quiet: print out

def _say(*msgs):
    _out('INFO', *msgs)

def _warn(*msgs):
    _out('WARN', *msgs)

def _error(*msgs):
    _out('ERROR', *msgs)

def _die(*msgs):
    _out('FATAL', *msgs)
    if not xb_exit_code: _exit_code(XB_EXIT_BY_DEATH)
    raise Exception(str(msgs))

def _debug(*msgs):
    if xb_opt_debug: _out("** DEBUG **", *msgs)

def _which(file):
    for path in os.environ["PATH"].split(os.pathsep):
        if os.path.exists(path + os.path.sep + file):
                return path + os.path.sep + file

    return None

def _parse_port_param(param):
    """
    Parses and assign given port range values 
    i.e.
    remote_nc_port = 9999
    remote_nc_port = 9999,1000
    """

    global xb_opt_remote_nc_port_min
    global xb_opt_remote_nc_port_max

    if not param: return False
    if param.isdigit():
        xb_opt_remote_nc_port_min = int(param)
        xb_opt_remote_nc_port_max = xb_opt_remote_nc_port_min
        return True
    elif param.count(',') == 1:
        pmin, pmax = param.split(',')
        pmin = pmin.strip()
        pmax = pmax.strip()
        if not pmin.isdigit() or not pmax.isdigit(): return False
        xb_opt_remote_nc_port_min = int(pmin)
        xb_opt_remote_nc_port_max = int(pmax)
        if xb_opt_remote_nc_port_min > xb_opt_remote_nc_port_max:
            pmin = xb_opt_remote_nc_port_max
            xb_opt_remote_nc_port_max = xb_opt_remote_nc_port_min
            xb_opt_remote_nc_port_min = pmin
        return True

    return False

def _read_magic_chunk(bfile, size):
    """
    This is a more reliable way of reading some files format

    XBCRYP for xbcrypt files
    XBSTCK for xbstream files
    """

    if not os.path.isfile(bfile):
        return None

    return open(bfile, 'rb').read(size)

def _check_binary(name):
    bin = _which(name)
    if bin is None:
        _die("%s script is not found in $PATH" % name)

    return bin

def _exit_code(code):
    global xb_exit_code

    c = int(code)
    if c > xb_exit_code: xb_exit_code = c

def _destroy_lock_file():
    if (xb_opt_command == XB_CMD_FULL or xb_opt_command == XB_CMD_INCR) \
            and os.path.isfile(XB_LCK_FILE):
        if xb_backup_in_progress is None:
            os.remove(XB_LCK_FILE)

def _create_lock_file():
    if (xb_opt_command == XB_CMD_FULL or xb_opt_command == XB_CMD_INCR):
        lck = open(XB_LCK_FILE, 'w')
        lck.write("backup = %s\n" % xb_curdate)
        lck.write("type = %s\n" % xb_opt_command)

        if xb_opt_command == XB_CMD_INCR:
            lck.write("full = %s\n" % xb_last_full)

        lck.write("pid = %s\n" % str(os.getpid()))

        lck.close()

def _xb_logfile_copy(bkp):
    # When backup is not compressed we need to preserve the 
    # xtrabackup_logfile since preparing directly from the
    # stor_dir will touch the logfile and we cannot use it
    # again
    # We do this to make the process faster instead of copying
    # the whole incremental backup
    _say("Preserving %s from %s" % (XB_LOG_FILE, bkp))
    xb_from = "%s/%s" % (bkp, XB_LOG_FILE)
    xb_to = "%s/%s.101" % (bkp, XB_LOG_FILE)
    shutil.copy(xb_from, xb_to)

def _xb_logfile_restore(bkp):
    _say("Restoring %s from %s" % (XB_LOG_FILE, bkp))
    xb_from = "%s/%s.101" % (bkp, XB_LOG_FILE)
    xb_to = "%s/%s" % (bkp, XB_LOG_FILE)
    if os.path.isfile(xb_to): os.remove(xb_to)
    shutil.move(xb_from, xb_to)

def _sigterm_handler(signal, frame):
    global XB_SIGTERM_CAUGHT

    _say("Got TERM signal, cleaning up ...")
    XB_SIGTERM_CAUGHT = True

def _check_in_progress():
    global xb_backup_in_progress

    ret = False
    is_backup = False

    if xb_opt_command in [XB_CMD_FULL, XB_CMD_INCR]:
        is_backup = True

    if os.path.isfile(XB_LCK_FILE):
        _debug("%s lock file exists and is_backup is %s" % (XB_LCK_FILE, str(is_backup)))

        cfp = _parse_raw_config(XB_LCK_FILE)
        pid = int(cfp.get(XB_BIN_NAME, 'pid'))
        xb_backup_in_progress = cfp
        ret = True

        if is_backup:
            try:
                os.kill(pid, 0)
            except OSError, e:
                if e.errno == errno.ESRCH:
                    _die("%s lock file exists but process is not running" % XB_LCK_FILE)
                elif e.errno == errno.EPERM:
                    _die('Permission denied while checking backup process')
                else:
                    _warn('Could not determine backup process state')
            else:
                _die("Another backup process in progress with PID %d" % pid)

    return ret

def _write_backup_info():
    global xb_backup_summary

    if (xb_opt_command == XB_CMD_FULL or xb_opt_command == XB_CMD_INCR):
        inf = open("%s/%s" % (xb_this_backup, XB_META_FILE), 'w')
        inf.write("backup = %s\n" % xb_curdate)
        inf.write("type = %s\n" % xb_opt_command)

        if xb_opt_command == XB_CMD_INCR:
            inf.write("full = %s\n" % xb_last_full)

        inf.write("start_backup = %s\n" % xb_curdate)
        inf.write("end_backup = %s\n" % xb_info_bkp_end)
        inf.write("start_prepare = %s\n" % xb_info_prep_start)
        inf.write("end_prepare = %s\n" % xb_info_prep_end)
        inf.write("compress = %d\n" % int(xb_opt_compress))
        inf.write("compress_with = %s\n" % xb_opt_compress_with)
        inf.write("log_bin = %s\n" % xb_this_binlog)
        inf.write("master_log_bin = %s\n" % xb_this_master_binlog)
        inf.write("last_lsn = %s\n" % xb_this_last_lsn)
        inf.write("source_version = %d.%d.%d\n" % (
            XB_VERSION_MAJOR, XB_VERSION_MINOR, XB_VERSION_REV))

        inf.close()

    if xb_opt_notify_on_success:
        xb_backup_summary = "Backup summary: \n\n"
        xb_backup_summary += "Backup: %s\n" % xb_curdate
        xb_backup_summary += "Type: %s\n" % xb_opt_command

        if xb_opt_command == XB_CMD_INCR:
            xb_backup_summary += "Full: %s\n" % xb_last_full

        xb_backup_summary += "Backup started: %s\n" % xb_curdate
        xb_backup_summary += "Backup ended: %s\n" % xb_info_bkp_end
        xb_backup_summary += "Prepare started: %s\n" % xb_info_prep_start
        xb_backup_summary += "Prepare ended: %s\n" % xb_info_prep_end
        xb_backup_summary += "Compressed: %s\n" % bool(xb_opt_compress)
        xb_backup_summary += "Compressed with: %s\n" % xb_opt_compress_with
        xb_backup_summary += "Binary log name: %s\n" % xb_this_binlog
        xb_backup_summary += "Master binary log name: %s\n" % xb_this_master_binlog

def _parse_raw_config(ckpnt_f):
    if not os.path.isfile(ckpnt_f):
        _warn("Config file not found, ", ckpnt_f, "!")
        return False

    with open(ckpnt_f) as ckp:
        defaults = dict([line.replace(' ','').rstrip("\n").split('=') for line in ckp])

    cfp = ConfigParser(defaults)
    cfp.add_section(XB_BIN_NAME)

    return cfp

def _read_backup_metadata(bkp):
    meta_path = os.path.join(bkp, XB_META_FILE)

    # For backwards compatibility
    if not os.path.isfile(meta_path):
        meta_path = os.path.join(bkp, 'xbackup.meta')

    meta = _parse_raw_config(meta_path)

    if not meta:
        _die("Unable to read backup meta information, ",
            "%s corrupt?" % meta_path)

    return meta

def _apply_log(bkp, incrdir=None, final=False):
    if not os.path.isdir(bkp):
        _warn("Directory not found, ", bkp, " will not prepare")
        return False

    cfp = _parse_raw_config("%s/xtrabackup_checkpoints" % bkp)
    if not cfp:
        _die('Could not parse xtrabackup_checkpoints file')

    ibx_cmd = ''
    ibx_log = "%s/%s-innobackupex-prepare.log" % (xb_opt_work_dir, xb_curdate)
    tee_cmd = "tee %s" % ibx_log

    ibx_opts = ""
    if XB_VERSION_MINOR >= 3:
        ibx_opts = '--prepare '
    else: ibx_opts = '--apply-log '

    ibx_opts += "--use-memory=%dM" % xb_opt_prepare_memory
    log_fd = None
    p_tee = None

    if not final:
        if XB_VERSION_MINOR >= 3: ibx_opts += " --apply-log-only"
        else: ibx_opts += " --redo-only"

    if cfp.get(XB_BIN_NAME,'backup_type') == 'incremental':
        _say('Preparing incremental backup: ', bkp)
        if XB_VERSION_MINOR >= 3: 
            ibx_opts += " --incremental-dir %s --target-dir %s" % (bkp, incrdir)
        else: ibx_opts += " --incremental-dir %s %s" % (bkp, incrdir)
    else:
        _say('Preparing full backup: ', bkp)
        if XB_VERSION_MINOR >= 3: ibx_opts += " --target-dir %s" % bkp
        else: ibx_opts += " %s" % bkp

    ibx_cmd = "%s %s" % (xb_ibx_bin, ibx_opts)
    _say("Running prepare command: ", ibx_cmd)

    try:
        if not xb_opt_debug:
            log_fd = os.open(ibx_log, os.O_WRONLY|os.O_CREAT)
            p_ibx = Popen(ibx_cmd, shell=True, stdout=PIPE, stderr=log_fd)
        else:
            p_ibx = Popen(ibx_cmd, shell=True, stdout=PIPE, stderr=PIPE)
            p_tee = Popen(tee_cmd, shell=True, stdin=p_ibx.stderr)

        r = p_ibx.poll()
        while r is None:
            time.sleep(2)
            r = p_ibx.poll()

        if p_tee is not None: p_tee.wait()

        if log_fd is not None:
            os.close(log_fd)

        if r != 0: raise Exception("Non-zero exit of innobackupex command!")

        if cfp.get(XB_BIN_NAME,'backup_type') == 'incremental':
            shutil.move(ibx_log,
                "%s/%s-innobackupex-prepare.log" % (incrdir, xb_curdate))
        else:
            shutil.move(ibx_log,
                "%s/%s-innobackupex-prepare.log" % (bkp, xb_curdate))

        return True

    except Exception, e:
        _error("Command was: ", ibx_cmd)
        _error("Error: process exited with status %s" % str(e))
        _error("Please check innobackupex log file at %s" % ibx_log)
        _exit_code(XB_EXIT_APPLY_FAIL)

    return False

def _prepare_backup(bkp, prep, final=False):
    prepare_success = False
    meta = _read_backup_metadata(bkp)

    if not meta:
        _die("Unable to read backup meta information, ",
            "%s corrupt?" % meta_f)

    is_cmp = bool(int(meta.get(XB_BIN_NAME, 'compress')))
    is_of_type = meta.get(XB_BIN_NAME, 'type')
    this_bkp = meta.get(XB_BIN_NAME, 'backup')

    # If the backup is compressed, we extract to the prepare path
    if is_cmp:
        prep_tmp = os.path.join(os.path.dirname(prep), this_bkp)
        if is_of_type == XB_CMD_FULL:
            if not os.path.isdir(prep): os.mkdir(prep, 0755)
            cmp_to = prep
        else:
            if not os.path.isdir(prep_tmp): os.mkdir(prep_tmp, 0755)
            cmp_to = prep_tmp

        for fmt in ['xbs.gz', 'tar.gz', 'xbs.qp', 'xbs.qp.xbcrypt', 'qp', 'qp.xbcrypt']:
            bkp_file = "%s/backup.%s" % (bkp, fmt)
            if os.path.isfile(bkp_file):
                break

        _say("Decompressing %s" % bkp_file)
        if not _decompress(bkp_file, cmp_to, meta):
            _die("An error occurred while extracting %s to %s" % (bkp_file, cmp_to))

        if is_of_type == XB_CMD_FULL:
            _say("Applying log on %s" % prep)
            prepare_success = _apply_log(prep, prep, final)
        else:
            _say("Applying log on %s with %s" % (prep, prep_tmp))
            prepare_success = _apply_log(prep_tmp, prep, final)
            shutil.rmtree(prep_tmp)
    else:
        if is_of_type == XB_CMD_FULL:
            _say("Copying %s to %s" % (bkp, prep))
            shutil.copytree(bkp, prep)
            _say("Applying log to %s" % prep)
            prepare_success = _apply_log(prep, prep, final)
        else:
            _xb_logfile_copy(bkp)
            _say("Applying log on %s with %s" % (prep, bkp))
            prepare_success = _apply_log(bkp, prep, final)
            _xb_logfile_restore(bkp)

    return prepare_success

def _compress(bkp, archive):
    global xb_exit_code

    if not os.path.isdir(bkp):
        _warn("Directory not found, ", bkp, " cannot compress")
        return False

    if xb_opt_compress_with == 'gzip':
        return _compress_tgz(bkp, archive)
    elif xb_opt_compress_with == 'qpress':
        return _compress_qp(bkp, archive)

def _compress_qp(bkp, xbs):
    global xb_exit_code

    cwd = os.getcwd()
    os.chdir(bkp)

    # *.tar.gz tar+gzip compress either via innobackupex --stream=tar or
    #       tar czvf . -
    # *.qp cmopressed with qpress i.e. qpress -rvT4 .
    # *.xbs.qp for streamed qpress i.e. innobackupex --stream --compress
    # *.xbs.qp.xbcrypt for streamed qpress, encrypted 
    #   i.e. innobackupex --stream --compress --encrypt

    xbc_cmd = None
    qp = None
    xbc = None
    FNULL = None

    if xb_opt_debug:
        qp_cmd = 'qpress -rvT4'
    else:
        qp_cmd = 'qpress -rT4'

    if xb_opt_encrypt:
        qp_cmd += 'o .'
        xbc_cmd = 'xbcrypt --encrypt-algo=%s --encrypt-key-file=%s --output=%s.qp.xbcrypt' % (
            xb_opt_encrypt, xb_opt_encrypt_key_file, xbs)
        _debug("Encrypting with command: %s" % xbc_cmd)
    else:
        qp_cmd += ' . %s.qp' % xbs

    _debug("Compressing with command: %s" % qp_cmd)

    if not xb_opt_debug:
        FNULL = open(os.devnull, 'w')
        if xb_opt_encrypt:
            qp = Popen(qp_cmd, shell=True, stdout=PIPE, stderr=FNULL)
            xbc = Popen(xbc_cmd, shell=True, stdin=qp.stdout, stdout=FNULL, stderr=FNULL)
        else:
            qp = Popen(qp_cmd, shell=True, stdout=FNULL, stderr=STDOUT)
    else:
        if xb_opt_encrypt:
            qp = Popen(qp_cmd, shell=True, stdout=PIPE)
            xbc = Popen(xbc_cmd, shell=True, stdin=qp.stdout)
        else:    
            qp = Popen(qp_cmd, shell=True)

    r = qp.poll()
    while r is None:
        time.sleep(5)
        r = qp.poll()

    if xbc is not None:
        x = xbc.poll()
        if x is None: xbc.wait()
        x = xbc.poll()

    if FNULL is not None:
        FNULL.close()

    if r != 0:
        _error("Compressing ", bkp, " to ", xbs, " failed.")
        _error("qpress command was: ", qp_cmd)
        _error("qpress returned exit code was: ", str(r))

        if xb_opt_encrypt:
            _error("xbcrypt command was: ", xbc_cmd)
            _error("xbcrypt returned exit code was: ", str(x))

        _exit_code(XB_EXIT_COMPRESS_FAIL)
        return False

    os.chdir(cwd)

    return True

def _compress_tgz(bkp, tgz):
    global xb_exit_code

    tgz = "%s.tar.gz" % tgz

    if os.path.isfile(tgz):
        _warn("Destination archive already exists, ", tgz, " aborting compression")
        return False

    cwd = os.getcwd()
    os.chdir(bkp)

    run_cmd = "tar c"
    run_cmd += 'z'
    if xb_opt_debug:
        run_cmd += 'v'

    run_cmd += "f %s %s" % (tgz, './')
    FNULL = None

    _debug("Running compress command: %s" % run_cmd)

    if not xb_opt_debug:
        FNULL = open(os.devnull, 'w')
        p1 = Popen(run_cmd, shell=True, stdout=FNULL, stderr=STDOUT)
    else:
        p1 = Popen(run_cmd, shell=True)

    r = p1.poll()
    while r is None:
        time.sleep(5)
        r = p1.poll()

    if FNULL is not None:
        FNULL.close()

    os.chdir(cwd)

    if r != 0:
        _error("Compressing ", bkp, " to ", tgz, " failed.")
        _error("tar command was: ", run_cmd)
        _error("tar returned exit code was: ", str(r))
        _exit_code(XB_EXIT_COMPRESS_FAIL)
        return False

    return True

def _extract_tgz(tgz, dest):
    run_cmd = "tar xi"
    if xb_opt_compress_with == 'gzip':
        run_cmd += 'z'
    if xb_opt_debug:
        run_cmd += 'v'

    run_cmd += "f %s -C %s" % (tgz, dest)
    FNULL = None

    if not xb_opt_debug:
        FNULL = open(os.devnull, 'w')
        p1 = Popen(run_cmd, shell=True, stdout=FNULL, stderr=STDOUT)
    else:
        p1 = Popen(run_cmd, shell=True)

    r = p1.poll()
    while r is None:
        time.sleep(5)
        r = p1.poll()

    if FNULL is not None:
        FNULL.close()

    if r != 0:
        _error("Extracting ", tgz, " to ", dest, " failed.")
        _error("tar command was: ", run_cmd)
        _error("tar returned exit code was: ", str(r))
        _exit_code(XB_EXIT_EXTRACT_FAIL)
        return False

    return True

def _extract_xgz(xgz, dest):
    gz_cmd = "gzip -cd"
    #if xb_opt_debug:
    #    gz_cmd += ' -v'

    gz_cmd += " %s" % xgz
    FNULL = None

    xbs_cmd = "xbstream -x -C %s" % dest

    _debug("Running gzip command: %s" % gz_cmd)
    _debug("Running xbstream command: %s" % xbs_cmd)

    if not os.path.isdir(dest): os.mkdir(dest, 0755)

    if not xb_opt_debug:
        FNULL = open(os.devnull, 'w')
        gz = Popen(gz_cmd, shell=True, stdout=PIPE, stderr=FNULL)
        xbs = Popen(xbs_cmd, shell=True, stderr=FNULL, stdin=gz.stdout)
    else:
        gz = Popen(gz_cmd, shell=True, stdout=PIPE)
        xbs = Popen(xbs_cmd, shell=True, stdin=gz.stdout)

    r = gz.poll()
    while r is None:
        time.sleep(5)
        r = gz.poll()

    x = xbs.poll()
    if x is None: xbs.wait()
    x = xbs.poll()

    if FNULL is not None:
        FNULL.close()

    if r != 0:
        _error("Extracting ", xgz, " to ", dest, " failed.")
        _error("Extract command was: %s | %s" % (gz_cmd, xbs_cmd))
        _error("Extract returned exit codes were: %s and %s" % (str(r), str(x)))
        _exit_code(XB_EXIT_EXTRACT_FAIL)
        return False

    return True

def _extract_xbs(xbs, dest, meta = None):
    xbs_cmd = "xbstream -x -C %s" % dest
    xbc_cmd = 'cat %s' % xbs

    if not os.path.isdir(dest): os.mkdir(dest, 0755)

    _say("Extracting from xbstream format: %s" % xbs)
    FNULL = None

    if not xb_opt_debug:
        FNULL = open(os.devnull, 'w')
        xbc = Popen(xbc_cmd, shell=True, stdout=PIPE, stderr=FNULL)
        xbs = Popen(xbs_cmd, shell=True, stderr=FNULL, stdin=xbc.stdout)
    else:
        xbc = Popen(xbc_cmd, shell=True, stdout=PIPE)
        xbs = Popen(xbs_cmd, shell=True, stdin=xbc.stdout)

    r = xbc.poll()
    while r is None:
        time.sleep(5)
        r = xbc.poll()

    x = xbs.poll()
    if x is None: xbs.wait()
    x = xbs.poll()

    if FNULL is not None:
        FNULL.close()

    if r != 0:
        _error("Extracting ", xbs, " to ", dest, " failed.")
        _error("Extract command was: %s | %s" % (xbc_cmd, xbs_cmd))
        _error("Extract returned exit codes were: %s and %s" % (str(r), str(x)))
        _exit_code(XB_EXIT_EXTRACT_FAIL)
        _die("Decompress of xbstream file %s failed." % xbs)

    return True

def _extract_xbcrypt(dest, meta = None):
    """ Decrypt a backup set encrypted with xbcrypt
    if xrabackup version is < 2.3 we use xtrabackup --decrypt
    via _extract_xbcrypt_file which decrypts the files one
    at a timedelta
    """

    if _xb_version(tof = True) < 2.3:
        _say(
            "You are running an older xtrabackup version "
            "that do not have --decrypt support, "
            "switching manual decompresssion")
        return _extract_xbcrypt_file(dest)

    # Now we decompress *.xbcrypt files
    ibx_cmd = '%s --decrypt=%s --encrypt-key-file=%s --target-dir=%s' % (
        xb_ibx_bin, xb_opt_encrypt, xb_opt_encrypt_key_file, dest)

    FNULL = None

    if not xb_opt_debug:
        FNULL = open(os.devnull, 'w')
        ibx = Popen(ibx_cmd, shell=True, stdout=FNULL, stderr=FNULL)
    else:
        ibx = Popen(ibx_cmd, shell=True)

    r = ibx.poll()
    while r is None:
        time.sleep(5)
        r = ibx.poll()

    if FNULL is not None:
        FNULL.close()

    if r != 0:
        _error("Decrypt of backup failed.")
        _error("Decrypt command was: %s" % ibx_cmd)
        _error("Decrypt returned exit code was: %s" % str(r))
        _exit_code(XB_EXIT_DECRYPT_FAIL)
        _die("Decrypt of backup %s failed." % dest)

    _cleanup_files_by_ext(dest, 'xbcrypt')

    return True

def _extract_xbcrypt_file(cfile):
    """ cfile is encrypted file or folder, this method
    traverses individual xbcrypt files if --decrypt option is
    not available
    """

    if os.path.isdir(cfile):
        _debug("Decrypting from directory: %s" % cfile)
        ls = os.listdir(cfile)
        for f in ls:
            _extract_xbcrypt_file(os.path.join(cfile, f))

    elif os.path.isfile(cfile) and '.xbcrypt' == cfile[-8:]:
        xbc_cmd = 'xbcrypt --decrypt --encrypt-algo=%s ' % xb_opt_encrypt
        xbc_cmd += '--encrypt-key-file=%s --input=%s --output=%s' % (
            xb_opt_encrypt_key_file, cfile, cfile[:-8])

        FNULL = None

        _debug("Decrypting from xbcrypt file: %s" % cfile)

        if not xb_opt_debug:
            FNULL = open(os.devnull, 'w')
            xbc = Popen(xbc_cmd, shell=True, stdout=FNULL, stderr=FNULL)
        else:
            xbc = Popen(xbc_cmd, shell=True)

        r = xbc.poll()
        t = 0.1
        while r is None:
            time.sleep(t)
            # Increase time per loop
            if t <= 5: t = t*1.5
            r = xbc.poll()

        if FNULL is not None:
            FNULL.close()

        if r != 0:
            _error("Extracting ", cfile, " failed.")
            _error("Extract command was: %s" % xbc_cmd)
            _error("Extract returned exit codes were: %s" % str(r))
            _exit_code(XB_EXIT_DECRYPT_FAIL)
            _die("Decrypt of file %s failed." % cfile)

        os.remove(cfile)

    else: 
        _warn("File %s is not a valid xxbcrypt file." % cfile)

    return True

def _extract_stream_qpress(xbs, dest, meta = None):
    xbc_cmd = None
    is_encrypted = False

    xbs_cmd = "xbstream -x -C %s" % dest

    if xbs[-14:] == 'xbs.qp.xbcrypt':
        if not xb_opt_encrypt:
            _die("Backup is %s is encrypted, no encryption options provided" % qp)
        else:
            xbc_cmd = 'xbcrypt --decrypt --encrypt-algo=%s ' % xb_opt_encrypt
            xbc_cmd += '--encrypt-key-file=%s --input=%s' % (xb_opt_encrypt_key_file, xbs)

        is_encrypted = True

        """ xtrabackup 2.3+ made a change with streamed encrypted
        backups
        """
        if _read_magic_chunk(xbs, 6) == 'XBSTCK':
            _extract_xbs(xbs, dest, meta)
            _extract_xbcrypt(dest, meta)
            return _extract_ibx_decompress(dest, meta)
    else:
        xbc_cmd = 'cat %s' % xbs

    FNULL = None

    _debug("Running xbcrypt command: %s" % xbc_cmd)
    _debug("Running xbstream command: %s" % xbs_cmd)

    if not os.path.isdir(dest): os.mkdir(dest, 0755)

    if not xb_opt_debug:
        FNULL = open(os.devnull, 'w')
        xbc = Popen(xbc_cmd, shell=True, stdout=PIPE, stderr=FNULL)
        xbs = Popen(xbs_cmd, shell=True, stderr=FNULL, stdin=xbc.stdout)
    else:
        xbc = Popen(xbc_cmd, shell=True, stdout=PIPE)
        xbs = Popen(xbs_cmd, shell=True, stdin=xbc.stdout)

    r = xbc.poll()
    while r is None:
        time.sleep(5)
        r = xbc.poll()

    x = xbs.poll()
    if x is None: xbs.wait()
    x = xbs.poll()

    if FNULL is not None:
        FNULL.close()

    if r != 0:
        _error("Extracting ", xbs, " to ", dest, " failed.")
        _error("Extract command was: %s | %s" % (xbc_cmd, xbs_cmd))
        _error("Extract returned exit codes were: %s and %s" % (str(r), str(x)))
        _exit_code(XB_EXIT_EXTRACT_FAIL)
        return False

    return _extract_ibx_decompress(dest)

def _extract_nostream_qpress(qp, dest, meta = None):
    xbc_cmd = None
    is_encrypted = False

    if qp[-10:] == 'qp.xbcrypt':
        if not xb_opt_encrypt:
            _die("Backup is %s is encrypted, no encryption options provided" % qp)
        else:
            xbc_cmd = 'xbcrypt --decrypt --encrypt-algo=%s ' % xb_opt_encrypt
            xbc_cmd += '--encrypt-key-file=%s --input=%s' % (xb_opt_encrypt_key_file, qp)

        is_encrypted = True
        qp_cmd = 'qpress -di %s' % dest
    else:
        qp_cmd = 'qpress -d %s %s' % (qp, dest)

    FNULL = None

    _debug("Running qpress command: %s" % qp_cmd)
    if xbc_cmd is not None:
        _debug("Running xbcrypt command: %s" % xbc_cmd)

    if not os.path.isdir(dest): os.mkdir(dest, 0755)

    if is_encrypted:
        if not xb_opt_debug:
            FNULL = open(os.devnull, 'w')
            xbc = Popen(xbc_cmd, shell=True, stdout=PIPE, stderr=FNULL)
            qp = Popen(qp_cmd, shell=True, stderr=FNULL, stdin=xbc.stdout)
        else:
            xbc = Popen(xbc_cmd, shell=True, stdout=PIPE)
            qp = Popen(qp_cmd, shell=True, stdin=xbc.stdout)
    else:
        if not xb_opt_debug:
            FNULL = open(os.devnull, 'w')
            qp = Popen(qp_cmd, shell=True, stderr=FNULL, stdout=FNULL)
        else:
            qp = Popen(qp_cmd, shell=True)


    if is_encrypted:
        r = xbc.poll()
        while r is None:
            time.sleep(5)
            r = xbc.poll()

        x = qp.poll()
        if x is None: qp.wait()
        x = qp.poll()

        if FNULL is not None:
            FNULL.close()

        if r != 0:
            _error("Extracting ", qp, " to ", dest, " failed.")
            _error("Extract command was: %s | %s" % (xbc_cmd, qp_cmd))
            _error("Extract returned exit codes were: %s and %s" % (str(r), str(x)))
            _exit_code(XB_EXIT_EXTRACT_FAIL)
            return False
    else:
        r = qp.poll()
        while r is None:
            time.sleep(5)
            r = qp.poll()

        if FNULL is not None:
            FNULL.close()

        if r != 0:
            _error("Extracting ", qp, " to ", dest, " failed.")
            _error("Extract command was: %s" % qp_cmd, qp_cmd)
            _error("Extract returned exit codes was: %s" % str(r))
            _exit_code(XB_EXIT_EXTRACT_FAIL)
            return False

    return True

def _extract_qp_decompress(dest):
    """ this may not be efficient with millions of tables
    unlike how _extract_xbcrypt does it with recursive which
    avoids extra stat()
    """

    for root, dirs, files in os.walk(dest):
        for f in files:
            if f.endswith('.qp'):
                _debug("Found %s to decompress" % os.path.join(root, f))
                _extract_qp_file(os.path.join(root, f))
                os.remove(os.path.join(root, f))

    return True

def _extract_qp_file(file):
    qp_cmd = "qpress -d %s %s" % (file, os.path.dirname(file).rstrip('/'))
    qp = Popen(qp_cmd, shell=True)

    r = qp.poll()
    while r is None:
        time.sleep(0.5)
        r = qp.poll()

    if r != 0:
        _error("Decompressing %s failed." % file)
        _error("Extract command was: %s" % qp_cmd)
        _error("Extract returned exit code was: %s" % str(r))
        _exit_code(XB_EXIT_EXTRACT_FAIL)
        _die("Aborting")

    return True

def _extract_ibx_decompress(dest, meta = None):
    if (XB_VERSION_MAJOR == 2 and XB_VERSION_MINOR == 1 and XB_VERSION_REV < 4) or \
        XB_VERSION_MAJOR < 2 or (XB_VERSION_MAJOR == 2 and XB_VERSION_MINOR <= 0):
        _say(
            "You are running an older xtrabackup version "
            "that do not have --decompress support, "
            "switching manual decompresssion")
        return _extract_qp_decompress(dest)

    # Now we decompress *.qp files
    if xb_ibx_bin == 'xtrabackup':
        ibx_cmd = xb_ibx_bin + ' --decompress --target-dir=%s' % dest
    else:
        ibx_cmd = xb_ibx_bin + ' --decompress %s' % dest
    FNULL = None

    if not xb_opt_debug:
        FNULL = open(os.devnull, 'w')
        ibx = Popen(ibx_cmd, shell=True, stdout=FNULL, stderr=FNULL)
    else:
        ibx = Popen(ibx_cmd, shell=True)

    r = ibx.poll()
    while r is None:
        time.sleep(5)
        r = ibx.poll()

    if FNULL is not None:
        FNULL.close()

    if r != 0:
        _error("Decompressing *.qp files failed.")
        _error("Extract command was: %s" % ibx_cmd)
        _error("Extract returned exit code was: %s" % str(r))
        _exit_code(XB_EXIT_EXTRACT_FAIL)
        return False

    _cleanup_files_by_ext(dest, 'qp')

    return True

def _decompress(archive, dest, meta = None):
    if not os.path.isdir(dest):
        _warn("Destination directory not found, ", dest, " cannot compress")
        return False

    if not os.path.isfile(archive):
        _warn("Source archive does not exists, ", archive)
        return False

    if archive[-6:] == 'tar.gz':
        return _extract_tgz(archive, dest, meta)
    elif archive[-6:] == 'xbs.gz':
        return _extract_xgz(archive, dest, meta)
    elif archive[-6:]  == 'xbs.qp':
        return _extract_stream_qpress(archive, dest, meta)
    elif archive[-14:]  == 'xbs.qp.xbcrypt':
        return _extract_stream_qpress(archive, dest, meta)
    elif archive[-2:] == 'qp':
        return _extract_nostream_qpress(archive, dest, meta)
    elif archive[-10:] == 'qp.xbcrypt':
        return _extract_nostream_qpress(archive, dest, meta)
    else:
        _warn("Unknown archive format %s" % archive)
        return False

def _init_log_file(path, close=False, create=True):
    global xb_log_file
    global xb_log_fd

    _debug("Attempting init log from \"%s\" to \"%s\"" % (xb_log_file, path))

    if xb_opt_command is None or xb_opt_command in cmd_no_log:
        return True

    d = path.rstrip('/')
    if os.path.isdir(d):
        _warn("New log file path must be a full path to file name, ",
            "directory is given %s, aborting log init" % d)
        return False
    elif os.path.isfile(d):
        if d == xb_log_file:
            _debug("New log file is the same is current")
            if not close and xb_log_fd is None:
                _debug("Log file is not open, opening ...")
                xb_log_fd = os.open(d, os.O_WRONLY|os.O_APPEND)

            return True
        else:
            _debug("New log file exists, %s, aborting log init" % d)
            return False

    if os.path.isfile(xb_log_file):
        _debug("Renaming log file from %s to %s" % (xb_log_file, d))

        if close and xb_log_fd is not None: os.close(xb_log_fd)
        return True

        if xb_log_fd is not None: os.close(xb_log_fd)

        shutil.move(xb_log_file, d)
        xb_log_file = d

        if not close: xb_log_fd = os.open(d, os.O_WRONLY|os.O_APPEND)
    else:
        if create:
            xb_log_file = path
            _debug("Opening new log file %s" % path)
            xb_log_fd = os.open(path, os.O_WRONLY|os.O_CREAT)
            _debug("Logging to %s" % xb_log_file)
            _debug("Log fd: %s" % str(xb_log_fd))
        else:
            _warn("Log file %s does not exist, aborting rename" % xb_log_file)

def _cleanup_files_by_ext(fpath, ext):
    if os.path.isdir(fpath):
        ls = os.listdir(fpath)
        for f in ls:
            _cleanup_files_by_ext(os.path.join(fpath, f), ext)
    elif os.path.isfile(fpath) and fpath.endswith(".%s" % ext):
        os.remove(fpath)
    #else:
    #    _debug("Path does not match extension %s" % fpath)

def _cleanup_dir(folder, excludes = []):
    _say("Cleaning up %s excluding %s" % (folder, str(excludes)))
    if not os.path.isdir(folder):
        _warn("Cannot cleanup %s, directory does not exist" % folder)
        return False

    l = os.listdir(folder)
    if len(l) <= 0: return True

    for d in l:
        if d in excludes: continue

        f = os.path.join(folder, d)
        _debug("Deleting %s" % f)
        if os.path.isfile(f): os.remove(f)
        else: shutil.rmtree(f)

def _get_binlog_info_from_log(logfile):
    global xb_this_binlog
    global xb_this_master_binlog

    if not os.path.isfile(logfile): return False

    with open(logfile, "r") as f:
        f.seek (0, 2)
        fsize = f.tell()
        f.seek (max (fsize-1024, 0), 0)
        lines = f.readlines()

    lines = lines[-20:]
    m = None
    s = None

    for l in lines:
        if 'MySQL binlog position' in l:
            m = re.search('filename \'(.*)\',', l)
        
        if 'MySQL slave binlog position' in l:
            s = re.search('filename \'(.*)\'', l)

    if m is not None:
        _say("Found binary log name from log %s" % m.group(1))
        xb_this_binlog = m.group(1)

    if s is not None:
        _say("Found master binary log name from log %s" % s.group(1))
        xb_this_master_binlog = s.group(1)

def _notify_by_email(subject, msg="", to=None):
    try:
        if to is not None:
            recpt = to
        else:
            recpt = xb_opt_notify_by_email

        if os.path.isfile(xb_log_file):
            fp = open(xb_log_file, 'rb')
            log = fp.read()
            fp.close()
            msg = "%s\n\n%s" % (msg, log)

        fr = "%s@%s" % (xb_user, xb_hostname)
        hdr = "From: %s\n" % fr
        hdr += "To: %s\n" % recpt
        hdr += "Subject: %s\n\n" % subject
        s = smtplib.SMTP('127.0.0.1')
        s.sendmail(fr, recpt.split(','), hdr + msg)
        s.quit()
    except Exception, e:
        if xb_opt_debug: traceback.print_exc()
        _die("Could not send mail ({0}): {1}".format(e.errno, e.strerror))

    return True

def _ssh_execute(cmd, out=False, nowait=False):
    r_cmd = "(%s) || echo 'CMD_FAIL'" % cmd
    
    if xb_opt_debug:
        ssh_cmd = "ssh -o PasswordAuthentication=no"
    else: ssh_cmd = "ssh -o PasswordAuthentication=no -q"

    ssh_cmd = "%s %s %s@%s \"%s\"" % (
        ssh_cmd, xb_opt_ssh_opts, xb_opt_ssh_user, xb_opt_remote_host, r_cmd)
    _debug("Executing remote command %s" % ssh_cmd)

    tee_cmd = "tee %s" % XB_SSH_TMPFILE
    p_tee = None

    try:
        if nowait:
            Popen(ssh_cmd, shell=True, close_fds=True)
            return True

        tmp_fd = os.open(XB_SSH_TMPFILE, os.O_WRONLY|os.O_CREAT|os.O_TRUNC)
        
        if out and not xb_opt_quiet:
            p = Popen(ssh_cmd, shell=True, stdout=PIPE)
            p_tee = Popen(tee_cmd, shell=True, stdin=p.stdout)
        else:
            p = Popen(ssh_cmd, shell=True, stdout=tmp_fd, stderr=tmp_fd)

        r = p.poll()
        while r is None:
            time.sleep(1)
            r = p.poll()

        os.close(tmp_fd)
        if p_tee is not None:
            p_tee.wait()

        if r != 0:
            _error("SSH command failed with error code %s" % str(r))
            _exit_code(XB_EXIT_REMOTE_CMD_FAIL)
            return False

        f = open(XB_SSH_TMPFILE)
        l = f.readlines()
        if len(l) > 0:
            err = l[-1:][0].rstrip('\n')
            if 'CMD_FAIL' == err:
                err = ', '.join(i.rstrip('\n') for i in l)
                _error("Remote command failed \"%s\"" % err)
                _exit_code(XB_EXIT_REMOTE_CMD_FAIL)
                return False
            else:
                return l[0:][0].rstrip('\n')

        return True

    except Exception, e:
        _error("Command was: ", ssh_cmd)
        _error("Error: process exited with status %s" % str(e))
        _exit_code(XB_EXIT_REMOTE_CMD_FAIL)
        raise

def _pre_run_xb():
    global xb_last_backup
    global xb_last_backup_is
    global xb_last_full

    if xb_opt_remote_push_only:
        t = _ssh_execute(
            "%s -q --meta-item=xb_last_backup,xb_last_backup_is,xb_last_full meta" % xb_opt_remote_script
        )
        if t: t = t.split(' ')
        if len(t) < 3:
            _die("Unable to determine previous backup information from remote")

        xb_last_backup, xb_last_backup_is, xb_last_full = t

def _oldest_binlog_from_backup():
    old_binlog = False

    if xb_opt_binlog_from_master:
        field_name = 'master_log_bin'
    else:
        field_name = 'log_bin'

    if xb_full_list is None or len(xb_full_list) <= 0:
        return False

    # Backward compatibility with 'xbackup.meta'
    meta_file_dir = os.path.join(xb_stor_full, xb_full_list[-1:][0])
    meta = _read_backup_metadata(meta_file_dir)

    try:
        old_binlog = meta.get(XB_BIN_NAME, field_name)
        _debug("Found binlog from oldest full backup, %s" % old_binlog)

        if old_binlog == 'None':
            _warn("Invalid old binlog record from full backup, found '%s'" % old_binlog)
            old_binlog = False
    except NoOptionError, e:
        _warn("No binlog information from oldest full backup!")

    return old_binlog

def _purge_binlogs_to(old_binlog):
    if xb_binlogs_list is None: return

    if xb_opt_retention_binlogs is None:
        for l in xb_binlogs_list:
            if l < old_binlog:
                _say("Deleting old binary log %s" % l)
                os.remove(os.path.join(xb_stor_binlogs, l))
    else:
        x = int(time.time())-(xb_opt_retention_binlogs*24*60*60)
        for l in xb_binlogs_list:
            f = os.path.join(xb_stor_binlogs, l)
            b = open(f, 'rb')
            b.seek(4)
            ts = unpack('I', b.read(4))[0]
            ts_out = str(datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S'))
            _debug("%s created at %s" % (l, ts_out))
            b.close()

            if ts < x: 
                _debug("Pruning %s" % l)
                os.remove(f)
            else: 
                _say("%s matches binary log retention period, stopping" % l)
                break

def _purge_bitmaps_to(lsn):
    _say("Purging bitmap files to LSN: %s" % lsn)

    if not db_connect():
        _error("Failed to connect to server, unable to purge bitmaps.")
        _exit_code(XB_EXIT_BITMAP_PURGE_FAIL)
        return False

    try:
        cur = xb_mysqldb.cursor(MySQLdb.cursors.DictCursor)
        cur.execute("PURGE CHANGED_PAGE_BITMAPS BEFORE %s" % lsn)
    except MySQLdb.OperationalError, e:
        _error("Got MySQL error %d, \"%s\" at execute" % (e.args[0], e.args[1]))
        _error("Failed to purge bitmaps!")
        _exit_code(XB_EXIT_BITMAP_PURGE_FAIL)
        return False

    return True

def _find_open_port():
    """
    Check for an open port via netstat
    """

    netstat = _which('netstat')
    nc = _which('nc')
    awk = _which('awk')
    grep = _which('grep')
    port = False

    for p in range(xb_opt_remote_nc_port_min, xb_opt_remote_nc_port_max+1):
        netstat_cmd = Popen([netstat, '-plnt'], stdout=PIPE)
        awk_cmd = Popen([awk, ' {print $4}'], stdin=netstat_cmd.stdout, stdout=PIPE)
        grep_cmd = Popen([grep, "\:%d" % p], stdin=awk_cmd.stdout, stdout=PIPE)
        grep_cmd.communicate()

        if int(grep_cmd.returncode) > 0:
            port = p
            break
        else: continue

    return port

def _is_remote_nc_port_open(port):
    # We only look for on specific line on the process tree
    # this can be improved in the future
    is_open = _ssh_execute(
        "ps -f -u %s | egrep 'nc -l %d$'|wc -l" % (xb_opt_ssh_user, port))

    return int(is_open)

def _open_remote_nc_port(port, pipe_cmd):
    is_open = _is_remote_nc_port_open(port)
    
    if is_open == 1:
        _die("The requested port is already open on the remote server")
    
    for i in range(1, 4):
        _ssh_execute(
            "nc -l %d | %s" % (port, pipe_cmd), 
            nowait=True)
        time.sleep(5)

        is_open = _is_remote_nc_port_open(port)
        _debug("Got %d from remote port check" % is_open)
        
        if is_open == 1: break
        if is_open == 0 and xb_opt_debug:
            _debug("Failed to open remote nc port after %d attempt" % i)

    if is_open == 0:
        _die("Could not open netcat port on remote server after 3 attempts")

    return True

def _close_remote_nc_port(port):
    is_open = 0

    for i in range(1, 4):
        _ssh_execute(
            "echo 'FORCE_CLOSE' | nc localhost %d" % port, 
            nowait=True)
        time.sleep(3)

        is_open = _is_remote_nc_port_open(port)
        if is_open == 0: break

        if is_open == 1 and xb_opt_debug:
            _debug("Failed to close remote nc port after %d attempt" % i)

    if is_open == 1:
        _debug("Failed to close remote netcat port after multiple attempts")
        return False

def _push_to_remote_scp(src, dst):
    """
    Push a backup folder to a remote location via scp or netcat
    """

    global xb_exit_code

    FNULL = None
    p_scp = None

    # Unfortunately rsync will not work on all platforms with closed
    # security policies, we will add this as an option in the future.
    #run_cmd = "rsync -avz -e 'ssh -o PasswordAuthentication=no -q %s' %s %s@%s:%s" % (
    run_cmd = "scp -r -o PasswordAuthentication=no -q %s %s %s@%s:%s" % (
            xb_opt_ssh_opts, src, xb_opt_ssh_user, xb_opt_remote_host, dst
        )

    _say("Pushing %s to remote host %s:%s" % (src, xb_opt_remote_host, dst))
    _debug("Push command is: %s" % str(run_cmd))

    if not xb_opt_debug:
        FNULL = open(os.devnull, 'w')
        p_scp = Popen(run_cmd, shell=True, stdout=FNULL, stderr=STDOUT)
    else:
        p_scp = Popen(run_cmd, shell=True)

    r = p_scp.poll()
    while r is None:
        time.sleep(3)
        r = p_scp.poll()

    if FNULL is not None:
        FNULL.close()

    if r != 0:
        _error("Pushing ", src, " to remote ", dst, " failed.")
        _error("Push command was: ", run_cmd)
        _error("scp returned exit code was: ", str(r))
        _exit_code(XB_EXIT_REMOTE_PUSH_FAIL)
        return False

    return True

def _push_to_remote_netcat(src, dst):
    """
    Push a file or a directory to a remote destination.
    """

    global xb_exit_code

    FNULL = None
    p_nc = None

    nc_cmd = "nc %s %d" % (xb_opt_remote_host, xb_opt_remote_nc_port_min)

    if os.path.isdir(src):
        os.chdir(src)
        tar_cmd = "tar -czf - ." 
    else:
        os.chdir(os.path.dirname(src))
        tar_cmd = "tar -czf - %s" % os.path.basename(src)

    _say("Pushing %s to remote host %s:%s" % (src, xb_opt_remote_host, dst))
    _debug("Push command is: %s" % str(nc_cmd))

    _open_remote_nc_port(xb_opt_remote_nc_port_min, "tar -C %s -xzf -" % dst)

    if not xb_opt_debug:
        FNULL = open(os.devnull, 'w')
        p_tar = Popen(tar_cmd, shell=True, stdout=PIPE, stderr=FNULL)
        p_nc = Popen(nc_cmd, shell=True, stdin=p_tar.stdout, stderr=FNULL)
    else:
        p_tar = Popen(tar_cmd, shell=True, stdout=PIPE)
        p_nc = Popen(nc_cmd, shell=True, stdin=p_tar.stdout)

    r = p_tar.poll()
    while r is None:
        time.sleep(3)
        r = p_tar.poll()

    x = p_nc.poll()
    if x is None: p_nc.wait()
    x = p_nc.poll()

    if FNULL is not None:
        FNULL.close()

    os.chdir(xb_cwd)

    if r != 0:
        _error("Pushing ", src, " to remote ", dst, " failed.")
        _error("Push command was: ", nc_cmd)
        _error("scp returned exit code was: ", str(r))
        _exit_code(XB_EXIT_REMOTE_PUSH_FAIL)
        return False

    return True

def run_wipeout():
    if not xb_opt_wipeout:
        _warn("*************************************************")
        _warn("Warning! This is a dangerous option and it will wipe out ",
            "all traces of any backups. If you are sure, specify ",
            "--i-am-absolutely-sure-wipeout here too!")
        _warn("*************************************************")
        return True

    _warn("**WIPEOUT** executing!")

    dirs = [xb_stor_full, xb_stor_incr, xb_stor_weekly, xb_stor_monthly,
            xb_stor_binlogs, os.path.join(xb_opt_stor_dir, 'tmp'),
            xb_opt_work_dir]

    for d in dirs:
        _say("Wiping out items from %s" % d)
        _cleanup_dir(d)

    _say("Done!")
    return True

def run_meta_query():
    v = []
    if xb_opt_meta_item:
        x = xb_opt_meta_item.split(',')
        for k in x:
            if k in globals() and globals()[k] is not None:
                v.append(globals()[k])
            else: v.append('NULL')

    if len(v) > 0:
        print ' '.join([str(i) for i in v])
    else: print 'NULL'

    return True

def run_xb():
    global xb_ibx_opts
    global xb_ibx_bin
    global xb_prepared_backup
    global xb_backup_is_success
    global xb_prepare_is_success
    global xb_this_backup
    global xb_this_backup_remote
    global xb_info_bkp_end
    global xb_info_prep_start
    global xb_info_prep_end
    global xb_this_last_lsn

    backup_fname = 'backup'
    backup_archive = None

    if xb_last_full:
        xb_prepared_backup = "%s/P_%s" % (xb_opt_work_dir, xb_last_full)


    if xb_opt_mysql_cnf:
        xb_ibx_opts = ' --defaults-file=' + xb_opt_mysql_cnf + ' ' + xb_ibx_opts

    if XB_VERSION_MINOR >= 3:
        xb_ibx_opts = ' --backup' + xb_ibx_opts        

    xb_ibx_opts = ' --no-timestamp' + xb_ibx_opts
    if xb_opt_mysql_user:
        xb_ibx_opts = (' --user=%s ' % xb_opt_mysql_user) + xb_ibx_opts

    if xb_opt_mysql_pass:
        xb_ibx_opts = (' --password=%s ' % xb_opt_mysql_pass) + xb_ibx_opts

    if xb_opt_mysql_host:
        xb_ibx_opts = (' --host=%s ' % xb_opt_mysql_host) + xb_ibx_opts

    if xb_opt_mysql_sock:
        xb_ibx_opts = (' --socket=%s ' % xb_opt_mysql_sock) + xb_ibx_opts

    if xb_opt_remote_push_only \
            and not _ssh_execute("mkdir -p %s" % xb_this_backup_remote):
        _die("Could not create remote directory to push backup to!")

    if xb_opt_compress and not xb_opt_apply_log:
        if xb_opt_compress_with == 'qpress':
            xb_ibx_opts += ' --compress --compress-threads=4'

        xb_ibx_opts += ' --stream=xbstream --parallel=4'
        xb_ibx_opts += ' --extra-lsndir=' + xb_this_backup
        os.mkdir(xb_this_backup)
    else:
        xb_ibx_opts += ' --parallel=4'

    if xb_opt_encrypt and not xb_opt_apply_log:
        xb_ibx_opts += ' --encrypt=%s --encrypt-threads=4 --encrypt-key-file=%s' % (
            xb_opt_encrypt, xb_opt_encrypt_key_file)

    # Check if rsync binary exists, if so let's use it for uncompressed 
    # on-streaming backups
    xb_rsync_bin = _which('rsync')
    if xb_rsync_bin is not None and \
            ((not xb_opt_compress and not xb_opt_remote_push_only) or \
            (xb_opt_apply_log)):
        xb_ibx_opts += ' --rsync'

    if xb_opt_extra_ibx_options is not None:
        xb_ibx_opts += ' ' + xb_opt_extra_ibx_options

    if XB_VERSION_MINOR >= 3:
        # --binlog-info on lp152764811
        xb_ibx_opts += ' --binlog-info=on --target-dir ' + xb_this_backup
    else:
        xb_ibx_opts += ' ' + xb_this_backup

    try:
        run_cmd = xb_ibx_bin + xb_ibx_opts

        pipe_cmd = ''
        p_cmp = None
        p_tee = None
        log_fd = None

        #if not xb_opt_debug:
        #    run_cmd += " 2> %s-xbackup.log" % xb_this_backup
        ibx_log = "%s/%s-innobackupex-backup.log" % (xb_opt_work_dir, xb_curdate)
        tee_cmd = "tee %s" % ibx_log

        if xb_opt_compress and not xb_opt_apply_log:
            if xb_opt_compress_with == 'qpress':
                backup_fname = 'backup.xbs.qp'
                pipe_cmd = "cat - >"
            else:
                backup_fname = 'backup.xbs.gz'
                pipe_cmd = "gzip - >"

            if xb_opt_encrypt:
                backup_fname += '.xbcrypt'

            if xb_opt_remote_push_only:
                backup_archive = "%s/%s" % (xb_this_backup_remote, backup_fname)
            else:
                backup_archive = "%s/%s" % (xb_this_backup, backup_fname)

            pipe_cmd = "%s %s" % (pipe_cmd, backup_archive)

            # We open the netcat port before opening the innobackupex process
            if xb_opt_remote_push_only and xb_opt_remote_nc_port_min:
                _open_remote_nc_port(xb_opt_remote_nc_port_min, pipe_cmd)

            if not xb_opt_debug:
                log_fd = os.open(ibx_log, os.O_WRONLY|os.O_CREAT)
                p_ibx = Popen(run_cmd, shell=True, stdout=PIPE, stderr=log_fd)
            else:
                p_ibx = Popen(run_cmd, shell=True, stdout=PIPE, stderr=PIPE)
                p_tee = Popen(tee_cmd, shell=True, stdin=p_ibx.stderr)

            if xb_opt_remote_push_only:
                if xb_opt_remote_nc_port_min:
                    pipe_cmd = "nc %s %d" % (
                        xb_opt_remote_host, xb_opt_remote_nc_port_min)
                else:
                    pipe_cmd = "ssh -o PasswordAuthentication=no -q %s %s@%s '%s'" % (
                        xb_opt_ssh_opts, xb_opt_ssh_user, xb_opt_remote_host, pipe_cmd)

                _debug('Piping backup to remote with "%s"' % pipe_cmd)
            else:
                _debug('Compressing backup with "%s"' % pipe_cmd)

            p_cmp = Popen(pipe_cmd, stdin=p_ibx.stdout, shell=True)
        elif xb_opt_remote_push_only:
            pipe_cmd = "xbstream -x -C %s" % xb_this_backup_remote

            # We open the netcat port before opening the innobackupex process
            if xb_opt_remote_nc_port_min:
                _open_remote_nc_port(xb_opt_remote_nc_port_min, pipe_cmd)

            if not xb_opt_debug:
                log_fd = os.open(ibx_log, os.O_WRONLY|os.O_CREAT)
                p_ibx = Popen(run_cmd, shell=True, stdout=PIPE, stderr=log_fd)
            else:
                p_ibx = Popen(run_cmd, shell=True, stdout=PIPE, stderr=PIPE)
                p_tee = Popen(tee_cmd, shell=True, stdin=p_ibx.stderr)

            if xb_opt_remote_nc_port_min:
                pipe_cmd = "nc %s %d" % (
                        xb_opt_remote_host, xb_opt_remote_nc_port_min)
            else:
                pipe_cmd = "ssh -o PasswordAuthentication=no -q %s %s@%s '%s'" % (
                    xb_opt_ssh_opts, xb_opt_ssh_user, xb_opt_remote_host, pipe_cmd)

            _debug('Piping backup to remote with "%s"' % pipe_cmd)
            p_cmp = Popen(pipe_cmd, stdin=p_ibx.stdout, shell=True)
        else:
            if not xb_opt_debug:
                log_fd = os.open(ibx_log, os.O_WRONLY|os.O_CREAT)
                p_ibx = Popen(run_cmd, shell=True, stderr=log_fd)
            else:
                p_ibx = Popen(run_cmd, shell=True, stderr=PIPE)
                p_tee = Popen(tee_cmd, shell=True, stdin=p_ibx.stderr)

        _say("Running xtrabackup with command: ", 
            re.sub('\s--password=([^\s]+)', ' --password=*******', run_cmd))

        r = p_ibx.poll()
        while r is None:
            time.sleep(2)
            r = p_ibx.poll()

        if p_cmp is not None: p_cmp.wait()
        if p_tee is not None: p_tee.wait()

        if log_fd is not None:
            os.close(log_fd)

        if r != 0: raise Exception("Non-zero exit of innobackupex command!")
        xb_backup_is_success = True
        xb_info_bkp_end = date(time.time(), '%Y_%m_%d-%H_%M_%S')

    except Exception, e:
        _error("Command was: ", run_cmd)
        _error("Error: process exited with status %s" % str(e))
        _error("Please check innobackupex log file at %s" % ibx_log)
        _exit_code(XB_EXIT_INNOBACKUP_FAIL)
        raise

    if xb_opt_command == XB_CMD_FULL and xb_backup_is_success:
        xb_full_list.insert(0, xb_curdate)

    if xb_backup_is_success:
        full_ckp = _parse_raw_config(os.path.join(xb_this_backup, XB_CKP_FILE))
        xb_this_last_lsn = full_ckp.get(XB_BIN_NAME, 'last_lsn')

    # Cleanup work directory
    # First, move the innobackupex logfile to the actual backup directory
    if xb_backup_is_success and not xb_opt_apply_log:
        shutil.move(ibx_log, "%s/innobackupex-backup.log" % xb_this_backup)
        ibx_log = "%s/innobackupex-backup.log" % xb_this_backup

    if xb_opt_apply_log and xb_backup_is_success:
        if xb_opt_command == XB_CMD_FULL and xb_prepared_backup \
                and os.path.isdir(xb_prepared_backup):
            _say("Removing previous prepared backup ", xb_prepared_backup)
            shutil.rmtree(xb_prepared_backup)

        xb_info_prep_start = date(time.time(), '%Y_%m_%d-%H_%M_%S')

        if xb_opt_command == XB_CMD_FULL:
            t = "%s/P_%s" % (xb_opt_work_dir, xb_curdate)
            shutil.copytree(xb_this_backup, t)
            xb_prepare_is_success = _apply_log(t, xb_this_backup)
        else:
            _xb_logfile_copy(xb_this_backup)
            xb_prepare_is_success = _apply_log(xb_this_backup, xb_prepared_backup)
            _xb_logfile_restore(xb_this_backup)

        xb_info_prep_end = date(time.time(), '%Y_%m_%d-%H_%M_%S')

        if xb_prepare_is_success:
            if xb_opt_command == XB_CMD_FULL:
                t = "%s/%s" % (xb_stor_full, xb_curdate)
            else:
                t = "%s/%s/%s" % (xb_stor_incr, xb_last_full, xb_curdate)

            if xb_opt_compress:
                _say("Post apply-log, compressing ", xb_this_backup)

                # Create base incremental folder if it does not exist yet
                if xb_opt_command == XB_CMD_INCR:
                    ib = os.path.join(xb_stor_incr, xb_last_full)
                    if not os.path.isdir(ib): os.mkdir(ib)

                if not os.path.isdir(t): os.mkdir(t)
                shutil.copy("%s/xtrabackup_checkpoints" % xb_this_backup,
                            "%s/xtrabackup_checkpoints" % t)
                _compress(xb_this_backup, "%s/backup" % t)
            else:
                _say("Post apply-log, copying ", xb_this_backup)
                if xb_opt_command == XB_CMD_FULL:
                    shutil.copytree(xb_this_backup, t)
                else:
                    shutil.copytree(xb_this_backup, t)

            # Update path to this backup to reflect movement to stor dir
            xb_this_backup = t

            shutil.move(ibx_log, "%s/innobackupex-backup.log" % t)
            ibx_log = "%s/innobackupex-backup.log" % t
            _say("Backup log has been moved to ", ibx_log)
        else:
            _die('Apply log failed, aborting!')
    else: xb_prepare_is_success = True

    # Let's grab our binary log information
    _get_binlog_info_from_log(ibx_log)
    # Let's write the backupe metadata info
    _write_backup_info()
    # Let's preserve our xbackup.log first
    #_init_log_file("%s/%s" % (xb_this_backup, XB_LOG_NAME))

    if xb_opt_remote_host:
        _ssh_execute("mkdir -p %s" % xb_this_backup_remote)

        if xb_opt_remote_nc_port_min:
            _push_to_remote_netcat(xb_this_backup, xb_this_backup_remote)
        else:
            _push_to_remote_scp(
                xb_this_backup,
                "%s/" % os.path.dirname(xb_this_backup_remote)
            ) 

    # Cleanup from our work directory to free up disk space.
    l = os.listdir(xb_opt_work_dir)
    # If xb_opt_apply_log is not enabled, we cleanup the whole work dir
    if not xb_opt_apply_log:
        excludes = [os.path.basename(xb_log_file)]
    else:
        excludes = [os.path.basename(ibx_log), "P_%s" % xb_curdate,
                    os.path.basename(XB_LCK_FILE),
                    os.path.basename(xb_log_file)]

        if xb_prepared_backup:
            excludes.append(os.path.basename(xb_prepared_backup))

    _cleanup_dir(xb_opt_work_dir, excludes)

    if xb_prepare_is_success and xb_backup_is_success:
        if xb_opt_remote_host:
            _ssh_execute("%s prune" % xb_opt_remote_script, True)

        prune_full_incr()
        prune_weekly()
        prune_monthly()

def run_xb_full():
    """Execute a full backup"""

    global xb_this_backup
    global xb_this_backup_remote

    _say("Running FULL backup, started at ",
        date(time.time(), '%Y-%m-%d %H:%M:%S'))

    if xb_opt_apply_log is False:
        xb_this_backup = os.path.join(xb_stor_full, xb_curdate)

        if os.path.isdir(xb_this_backup):
            _die(xb_this_backup, " backup directory already exists!")
    else:
        xb_this_backup = os.path.join(xb_opt_work_dir, xb_curdate)

    if xb_opt_remote_push_only:
        xb_this_backup = os.path.join(xb_opt_work_dir, xb_curdate)

    if xb_opt_remote_host:
        xb_this_backup_remote = os.path.join(xb_opt_remote_stor_dir, 'full', xb_curdate)

    run_xb()

def run_xb_incr():
    """Execute an incremental backup"""

    global xb_ibx_opts
    global xb_this_backup
    global xb_this_backup_remote

    _say("Running INCREMENTAL backup, started at ",
        date(time.time(), '%Y-%m-%d %H:%M:%S'))

    _pre_run_xb()
    if xb_last_full == 'NULL' or xb_last_full is None:
            _exit_code(XB_EXIT_NO_FULL)
            _die('Incremental backup requested, '
                'but there is no existing base full backup')

    if xb_opt_apply_log is False:
        xb_this_backup = os.path.join(xb_stor_incr, xb_last_full, xb_curdate)

        if os.path.isdir(xb_this_backup):
            _die(xb_this_backup, " backup directory already exists!")

        # Create base incremental folder if it does not exist yet
        ib = os.path.join(xb_stor_incr, xb_last_full)
        if not os.path.isdir(ib): os.mkdir(ib)

    else:
        xb_this_backup = os.path.join(xb_opt_work_dir, xb_curdate)

    if XB_VERSION_MINOR >= 3:
        xb_ibx_opts = ' --incremental-basedir='    
    else:
        xb_ibx_opts = ' --incremental --incremental-basedir='

    if xb_opt_remote_push_only:

        ib = os.path.join(xb_opt_work_dir, xb_last_backup)
        rb = os.path.join(xb_opt_remote_stor_dir, xb_last_backup_is)
        if xb_last_backup_is == XB_CMD_FULL:
            rb = os.path.join(rb, xb_last_backup)
        else:
            rb = os.path.join(rb, xb_last_full, xb_last_backup)

        if not os.path.isdir(ib): os.mkdir(ib)
        pull_from_remote(os.path.join(rb, XB_CKP_FILE), os.path.join(ib, XB_CKP_FILE))

        xb_this_backup = os.path.join(xb_opt_work_dir, xb_curdate)
    else:
        if xb_last_backup_is == XB_CMD_FULL:
            ib = os.path.join(xb_stor_full, xb_last_backup)
        else:
            ib = os.path.join(xb_stor_incr, xb_last_full, xb_last_backup)

    if xb_opt_remote_host:
        xb_this_backup_remote = os.path.join(
            xb_opt_remote_stor_dir, 'incr', xb_last_full, xb_curdate)

    xb_ibx_opts += ib

    run_xb()

    if xb_opt_purge_bitmaps:
        _purge_bitmaps_to(xb_this_last_lsn)

def run_xb_list():
    """List existing "valid backups"""

    if xb_opt_remote_push_only:
        return _ssh_execute("%s list" % xb_opt_remote_script, True)

    if len(xb_full_list) <= 0:
        _say("No backups currently available.")

    for f in xb_full_list:
        s = "# Full backup: " + f
        if f in xb_incr_list and xb_incr_list[f] and len(xb_incr_list[f]) > 0:
            s += ", incrementals: " + str(xb_incr_list[f])

        print s

    if xb_weekly_list is not None and len(xb_weekly_list) > 0:
        print "# Weekly list: %s" % str(xb_weekly_list)

    if xb_monthly_list is not None and len(xb_monthly_list) > 0:
        print "# Monthly list: %s" % str(xb_monthly_list)

    if xb_binlogs_list is not None and len(xb_binlogs_list) > 0:
        print "# Binary logs from %s to %s, %d total" % (
            xb_binlogs_list[0], xb_binlogs_list[-1], len(xb_binlogs_list))

def run_status():
    """Display status of last backup - excludes any currently running backup"""

    ret = 0
    txt = ''

    if xb_opt_remote_push_only: _pre_run_xb()

    if xb_backup_in_progress is not None:
        pid = int(xb_backup_in_progress.get(XB_BIN_NAME, 'pid'))
        bkp = xb_backup_in_progress.get(XB_BIN_NAME, 'backup')
        if pid <= 0:
            ret = 2
            txt = 'Invalid PID file found!'
        else:
            bkp_threshold = 24
            last_dt = datetime.strptime(bkp, '%Y_%m_%d-%H_%M_%S')
            old_dt = datetime.now() - timedelta(hours=bkp_threshold)

            try:
                os.kill(pid, 0)
            except OSError, e:
                if e.errno == errno.ESRCH:
                    ret = 2
                    txt = 'PID/lock file exists but process is not running'
                elif e.errno == errno.EPERM:
                    ret = 1
                    txt = 'Permission denied while checking backup process'
                else:
                    ret = 2
                    txt = 'Unknown backup process state'
            else:
                if last_dt < old_dt:
                    ret = 1
                    txt = "Backup has been running for more than %d hours" \
                        % bkp_threshold
                else: txt = "Backup process in progress with PID %d" % pid
    elif not xb_last_backup or xb_last_backup == 'NULL':
        ret = 2
        txt = 'No recent backup identified!'
    else:
        # We check how old our last backup is, for now itis hardcoded to a
        # threshold of 36 hours. If a full backup takes longer than 12hrs
        # then we should adjust this threshold
        dt_threshold = 36
        last_dt = datetime.strptime(xb_last_backup, '%Y_%m_%d-%H_%M_%S')
        old_dt = datetime.now() - timedelta(hours=dt_threshold)
        if last_dt < old_dt:
            ret = 1
            txt = "Last backup %s from %s is more than %d hours old" \
                % (xb_last_backup_is, xb_last_backup, dt_threshold)
        else:
            txt = "Last backup %s from %s" % (xb_last_backup_is, xb_last_backup)

    if ret == 0: txt = "OK - %s" % txt
    elif ret == 1: txt = "WARN - %s" % txt
    else: txt = "CRITICAL - %s" % txt

    if xb_opt_status_format == 'nagios': print txt
    elif xb_opt_status_format == 'zabbix': print ret
    sys.exit(ret)

def run_xb_restore_set(prepare_path=None, finalize=True):
    global xb_opt_restore_backup

    if xb_opt_restore_dir is None:
        _die('No prepare directory was specified, please specify a folder ',
            'where you want to stage the prepare.')
    if not os.path.isdir(xb_opt_restore_dir):
        _die("The specified prepare directory is not a valid directory")

    if xb_opt_restore_backup is None:
        xb_opt_restore_backup = xb_last_backup

    backup_is = XB_CMD_FULL
    the_backup = None
    the_backup_path = ''
    full_backup = None
    prepare_success = False

    # If xb_opt_restore_backup is specified, let's determine what kind of backup
    # it is
    if len(xb_full_list) <= 0:
        _die("No backups currently available.")

    for f in xb_full_list:
        # Break if we have already found the backup
        if the_backup is not None: break

        if f == xb_opt_restore_backup:
            the_backup = f
            break

        if f in xb_incr_list and len(xb_incr_list[f]) > 0:
            for i in xb_incr_list[f]:
                if i == xb_opt_restore_backup:
                    the_backup = i
                    full_backup = f
                    backup_is = XB_CMD_INCR
                    break

    if the_backup is None:
        _die("The specified backup to prepare was not found, check list")

    if not prepare_path:
        prepare_path = os.path.join(xb_opt_restore_dir, "P_%s" % the_backup)
    _say("Found backup %s to prepare of type %s" % (the_backup, backup_is))

    if os.path.isdir(prepare_path):
        _die("Cannot prepare from %s, directory already exists" % prepare_path)

    # If we are restoring the most recent backup and apply-log is enabled
    # for the backups, we can use the existing prepared backup in the
    # work directory
    #if the_backup == xb_last_backup and xb_opt_apply_log:
    #    the_backup_path = os.path.join(xb_opt_work_dir, "P_%s" % xb_last_full)
    #    if os.path.isdir(the_backup_path):
    #        _say("Using existing prepared backup for the restore")
    #        _say("Copying %s to %s" % (the_backup_path, prepare_path))
    #
    #        shutil.copytree(the_backup_path, prepare_path)

    if backup_is == XB_CMD_FULL:
        the_backup_path = os.path.join(xb_stor_full, the_backup)
        
        if finalize: 
            prepare_success = _prepare_backup(the_backup_path, prepare_path, finalize)
        else:
            prepare_success = _prepare_backup(the_backup_path, prepare_path)

        if not prepare_success:
            _die("There was a problem preparing full backup %s" % the_backup_path)

    elif backup_is == XB_CMD_INCR:
        # First let's work on the full backup for this incremental
        the_backup_path = os.path.join(xb_stor_incr, full_backup, the_backup)
        bkp_info = _read_backup_metadata(the_backup_path)

        bkp_full = bkp_info.get(XB_BIN_NAME, 'full')
        current_bkp = os.path.join(xb_stor_full, bkp_full)

        if not _prepare_backup(current_bkp, prepare_path):
            _die("There was a problem preparing base backup %s" % current_bkp)

        current_incr_list = xb_incr_list[bkp_full]
        current_incr_list.reverse()

        for current_bkp in current_incr_list:
            current_bkp_path = os.path.join(xb_stor_incr, bkp_full, current_bkp)

            if current_bkp == the_backup and finalize: 
                prepare_success = _prepare_backup(current_bkp_path, prepare_path, finalize)
            else:
                prepare_success = _prepare_backup(current_bkp_path, prepare_path)
            
            if not prepare_success:
                _die("There was a problem applying incremental backup ",
                    "%s to %s" % (current_bkp_path, prepare_path))

            if current_bkp == the_backup:
                # We call a final apply-log on the resulting
                # set if finalize == True
                if finalize: _apply_log(prepare_path, final = finalize)
                break

    _say("Prepare of backup %s successfully completed" % the_backup)

def run_xb_apply_last():
    global xb_opt_restore_dir

    # Get list of backups
    # Determine last backup
    # Check from work dir if P_ dir matches last full
    #   if not, cleanup and copy full or extract

    prepare_path = os.path.join(xb_opt_work_dir, "P_%s" % xb_last_full)
    if not os.path.isdir(prepare_path) \
            or not os.path.isfile(os.path.join(prepare_path, XB_CKP_FILE)):
        _cleanup_dir(xb_opt_work_dir)
        xb_opt_restore_dir = xb_opt_work_dir
        run_xb_restore_set(prepare_path, False)
        return True

    full_ckp = _parse_raw_config(os.path.join(prepare_path, XB_CKP_FILE))
    to_lsn = full_ckp.get(XB_BIN_NAME, 'to_lsn')

    if xb_last_full not in xb_incr_list or xb_incr_list[xb_last_full] is None:
        _say("No incremental backups taken for %s, we're done for now" % xb_last_full)
        return True

    i = xb_incr_list[xb_last_full]
    i.reverse()
    are_we_skippping = True

    for d in i:
        bkp_path = "%s/incr/%s/%s" % (xb_opt_stor_dir, xb_last_full, d)

        if are_we_skippping:
            ckp = _parse_raw_config("%s/%s" % (bkp_path, XB_CKP_FILE))
            _debug("Incremental %s, from_lsn: %s, to_lsn: %s, last_lsn: %s" % (
                bkp_path, ckp.get(XB_BIN_NAME, 'from_lsn'),
                ckp.get(XB_BIN_NAME, 'to_lsn'), to_lsn))
            if to_lsn > ckp.get(XB_BIN_NAME, 'from_lsn'):
                continue
            else:
                are_we_skippping = False

        if not _prepare_backup(bkp_path, prepare_path):
            _die("Apply log of incremental backup %s to %s failed" %
                (bkp_path, prepare_path))

    _say("Apply-last-log completed OK")

def run_binlog_stream():
    global xb_last_binlog
    global xb_first_binlog

    if xb_opt_binlog_binary is not None:
        if not os.path.isfile(xb_opt_binlog_binary):
            _die("The specified mysqlbinlog binary",
                "%s does not exist" % xb_opt_binlog_binary)
        else: mysqlbinlog = xb_opt_binlog_binary
    else: mysqlbinlog = 'mysqlbinlog'


    # Determine oldest binlog we should get based on xb_last_full log file
    # Determine latest binlog we have
    old_binlog = _oldest_binlog_from_backup()

    if not xb_first_binlog and not old_binlog and not xb_opt_first_binlog:
        _die("Cannot proceed, no binlog information from oldest backup nor ",
            "there are any existing binlogs yet. Please try with --first-binlog ",
            "option to specify the first binlog to start copying")

    if not old_binlog: old_binlog = xb_first_binlog

    # An explicit first-binlog takes precedence
    if xb_opt_first_binlog: old_binlog = xb_opt_first_binlog

    _say("Maintaing binary logs from %s" % old_binlog)

    if not db_connect():
        _die("Failed to connect to remote host, ", 
            "unable to check list of binary logs.")

    cur = xb_mysqldb.cursor(MySQLdb.cursors.DictCursor)
    cur.execute('SHOW BINARY LOGS')
    logs = []
    low = None
    high = None

    while True:
        row = cur.fetchone()
        if row is None: break
        logs.append(row['Log_name'])
        if low is None: low = row['Log_name']
        high = row['Log_name']

    db_close()
    if xb_last_binlog is None: xb_last_binlog = old_binlog

    _debug("old_binlog: %s, xb_last_binlog: %s, found_binlogs: %s" % (
        old_binlog, xb_last_binlog, str(logs)))

    if old_binlog not in logs and not int(old_binlog[-6:]) <= int(xb_last_binlog[-6:]):
        _die("I cannot find our oldest binlog from the available binary logs ",
            "on the server, aborting! Try again with --first-binlog")

    if xb_last_binlog not in logs:
        _die("I cannot find our newest binlog from the available binary logs ",
            "on the server, aborting! Try again with --first-binlog")

    run_cmd_s = mysqlbinlog

    if xb_opt_mysql_cnf is not None:
        run_cmd_s += " --defaults-file=%s" % xb_opt_mysql_cnf

    run_cmd_s += " --read-from-remote-server --raw --stop-never"

    if xb_opt_mysql_user is not None:
        run_cmd_s += " --user=%s" % xb_opt_mysql_user

    if xb_opt_mysql_pass is not None:
        run_cmd_s += " --password=%s" % xb_opt_mysql_pass

    if xb_opt_mysql_host is not None:
        run_cmd_s += " --host=%s" % xb_opt_mysql_host

    if xb_opt_mysql_port is not None:
        run_cmd_s += " --port=%s" % xb_opt_mysql_port

    run_failures = 0
    sleeps = 0
    poll = 15

    try:
        os.chdir(xb_stor_binlogs)

        while True:
            FNULL = None
            run_cmd = ("%s %s" % (run_cmd_s, xb_last_binlog))
            _debug("Running mysqlbinlog with: %s" % run_cmd)

            if xb_opt_debug:
                p = Popen(run_cmd, shell=True)
            else:
                FNULL = open(os.devnull, 'w')
                p = Popen(run_cmd, shell=True, stdout=FNULL, stderr=FNULL)

            pid_file = os.path.join('/tmp', "%s-binlog-stream.pid" % XB_BIN_NAME)
            pid_file_h = open(pid_file, 'w')
            pid_file_h.write(str(p.pid))
            pid_file_h.close()

            r = p.poll()
            while r is None:
                time.sleep(poll)
                sleeps += poll

                if sleeps >= 1800:
                    # We re-evaluate our oldest binlog to keep and purge older ones
                    list_backups()
                    old_binlog = _oldest_binlog_from_backup()
                    if not old_binlog: old_binlog = xb_first_binlog
                    _purge_binlogs_to(old_binlog)
                    _say("Maintaing binary logs from %s" % old_binlog)
                    sleeps = 0


                if XB_SIGTERM_CAUGHT:
                    p.kill()
                    sys.exit(0)

                r = p.poll()

            if FNULL is not None:
                FNULL.close()

            if r != 0:
                _error("mysqlbinlog command failed with error code %s" % str(r))
                _exit_code(XB_EXIT_BINLOG_STREAM_FAIL)
                run_failures += 1
                if run_failures == 10: return False
                else:
                    _say("Reconnecting mysqlbinlog ...")
                    list_binlogs()

        os.chdir(xb_cwd)
    except Exception, e:
        _error("Command was: ", run_cmd)
        _error("Error: process exited with status %s" % str(e))
        _exit_code(XB_EXIT_BINLOG_STREAM_FAIL)
        raise

    return True

def prune_full_incr():
    """Prune full/incremental sets from the store directory"""

    if len(xb_full_list) <= xb_opt_retention_sets: return True

    while len(xb_full_list) > xb_opt_retention_sets:
        d = xb_full_list.pop()

        if xb_is_last_day_of_week and xb_opt_retention_weeks > 0 \
                and len(xb_full_list) == xb_opt_retention_sets:
            # If today is the last day of the week, i.e Sunday
            # we will take our xb_opt_retention_sets + 1 backup
            # set and copy to our weekly folder
            _say("Rotating backup %s to weekly" % d)
            if d in xb_incr_list is not None:
                w = xb_incr_list[d][0]
            else: w = d

            w_dir = os.path.join(xb_stor_weekly, w)
            os.mkdir(w_dir, 0755)
            shutil.copytree(
                os.path.join(xb_stor_full, d), os.path.join(w_dir, 'full'))

            # If we have incremental backups, we copy them too
            if w != d:
                shutil.copytree(
                    os.path.join(xb_stor_incr, d), os.path.join(w_dir, 'incr'))

        if os.path.isdir(os.path.join(xb_stor_incr, d)):
            _say("Pruning incremental backup ", os.path.join(xb_stor_incr, d))
            shutil.rmtree(os.path.join(xb_stor_incr, d))

        if os.path.isdir(os.path.join(xb_stor_full, d)):
            shutil.rmtree(os.path.join(xb_stor_full, d))
            _say("Pruning full backup ", os.path.join(xb_stor_full, d))

def prune_weekly():
    """Prune weekly sets from the weekly store directory"""

    if xb_weekly_list is None \
            or len(xb_weekly_list) <= xb_opt_retention_weeks:
        return True

    while len(xb_weekly_list) > xb_opt_retention_weeks:
        d = xb_weekly_list.pop()
        w_dir = os.path.join(xb_stor_weekly, d)

        # If this weekly set has our end of the month backup
        # we rotate it first to monthly before deleting
        dt = datetime.strptime(d, '%Y_%m_%d-%H_%M_%S')
        m = dt - timedelta(days=6)

        if m.month < dt.month:
            _say("Rotating backup %s to monthly" % d)
            shutil.copytree(w_dir, os.path.join(xb_stor_monthly, d))

        shutil.rmtree(w_dir)

def prune_monthly():
    """Prune monthly monthly sets from monthly store directory"""

    if xb_monthly_list is None \
            or len(xb_monthly_list) <= xb_opt_retention_months:
        return True

    while len(xb_monthly_list) > xb_opt_retention_months:
        d = xb_monthly_list.pop()
        m_dir = os.path.join(xb_stor_monthly, d)

        if os.path.isdir(m_dir):
            _say("Pruning old monthly backup %s" % m_dir)
            shutil.rmtree(m_dir)

def pull_from_remote(src, dst):
    """Pull a file from remote via scp"""
    global xb_exit_code

    FNULL = None
    p_scp = None

    run_cmd = "scp -r -o PasswordAuthentication=no -q %s %s@%s:%s %s" % (
            xb_opt_ssh_opts, xb_opt_ssh_user, xb_opt_remote_host, src, dst
        )

    _say("Pulling %s from remote host %s:%s" % (src, xb_opt_remote_host, dst))

    if not xb_opt_debug:
        FNULL = open(os.devnull, 'w')
        p_scp = Popen(run_cmd, shell=True, stdout=FNULL, stderr=STDOUT)
    else:
        p_scp = Popen(run_cmd, shell=True)

    r = p_scp.poll()
    while r is None:
        time.sleep(5)
        r = p_scp.poll()

    if FNULL is not None:
        FNULL.close()

    if r != 0:
        _error("Pulling ", src, " from remote ", dst, " failed.")
        _error("Push command was: ", run_cmd)
        _error("rsync returned exit code was: ", str(r))
        _exit_code(XB_EXIT_EXTRACT_FAIL)
        return False

    return True

def db_connect():
    global xb_mysqldb

    params = dict()

    if xb_opt_mysql_user is not None:
        params['user'] = xb_opt_mysql_user

    if xb_opt_mysql_pass is not None:
        params['passwd'] = xb_opt_mysql_pass

    params['db'] = ''
    params['port'] = xb_opt_mysql_port

    if xb_opt_mysql_cnf is not None:
        params['read_default_file'] = xb_opt_mysql_cnf
        params['read_default_group'] = 'client'

    try:
        xb_mysqldb = MySQLdb.connect(xb_opt_mysql_host, **params)

        # MySQLdb for some reason has autoccommit off by default
        xb_mysqldb.autocommit(True)
    except MySQLdb.Error, e:
        _error("Error ", e.args[0], ": ", e.args[1])
        return False

    return xb_mysqldb

def db_close():
    global xb_mysqldb

    if xb_mysqldb is not None:
        xb_mysqldb.close()
        xb_mysqldb = None

def init():
    """Validate and populate all options/configuration values"""

    global xb_opt_config
    global xb_opt_config_section
    global xb_opt_mysql_host
    global xb_opt_mysql_user
    global xb_opt_mysql_pass
    global xb_opt_mysql_port
    global xb_opt_mysql_sock
    global xb_opt_mysql_cnf
    global xb_opt_stor_dir
    global xb_opt_work_dir
    global xb_opt_retention_binlogs
    global xb_opt_compress
    global xb_opt_compress_with
    global xb_opt_apply_log
    global xb_opt_prepare_memory
    global xb_opt_retention_sets
    global xb_opt_retention_months
    global xb_opt_retention_weeks
    global xb_opt_debug
    global xb_opt_quiet
    global xb_opt_status_format
    global xb_opt_command
    global xb_opt_restore_backup
    global xb_opt_restore_dir
    global xb_opt_remote_stor_dir
    global xb_opt_remote_host
    global xb_opt_remote_script
    global xb_opt_remote_push_only
    global xb_opt_remote_nc_port
    global xb_opt_ssh_opts
    global xb_opt_ssh_user
    global xb_opt_notify_by_email
    global xb_opt_notify_on_success
    global xb_opt_meta_item
    global xb_opt_wipeout
    global xb_opt_first_binlog
    global xb_opt_binlog_from_master
    global xb_opt_binlog_binary
    global xb_opt_encrypt
    global xb_opt_encrypt_key_file
    global xb_opt_extra_ibx_options
    global xb_opt_purge_bitmaps

    xb_opt_config = "/etc/%s.cnf" % XB_BIN_NAME

    if not os.path.isfile(xb_opt_config):
        xb_opt_config = "%s/%s.cnf" % (xb_cwd, XB_BIN_NAME)

    xb_opt_config_section = XB_BIN_NAME
    
    xb_cfg = None

    _init_log_file("/tmp/%s-%s" % (xb_curdate, XB_LOG_NAME))

    p_usage = "Usage: %prog [options] COMMAND"
    p_desc = "Managed xtrabackup based backups."
    p_epilog = """

Options here can also be specified on a filed called %s.cnf which will be 
checked in this order:

- on a file specified via the --config option
- /etc/pyxbackup.cnf
- on the same directory of the script 

Valid commands are:

    full: Execute full backups
    incr: Execute incremental backups
    list: List existing backups and additional information
    status: Check status of last backup
    apply-last: Prepare to the most recent backup
    restore-set: Restore to a specific backup set
    last-lsn: Print out to_lsn value of last backup for incremental use
    wipeout: Cleanup all existing backups

"""
    p_epilog = p_epilog % XB_BIN_NAME

    parser = PyxOptParser(p_usage, version="%prog " + str(xb_version),
        description=p_desc, epilog=p_epilog)
    parser.add_option('-f', '--config', dest='config', type='string',
        help='Path to config file to use, useful for multiple back locations')
    parser.add_option('', '--config-section', dest='config_section', type='string',
        help=('By default, config options are read from the %s section. '
            'If you have multiple sections/profile in the configuration file '
            'you can specify the section name, similar to mysql --defaults-group. '
            '(cli)')  % XB_BIN_NAME)
    parser.add_option('-u', '--mysql-user', dest='mysql_user', type='string',
        help='MySQL server username')
    parser.add_option('-p', '--mysql-pass', dest='mysql_pass', type='string',
        help='MySQL server password')
    parser.add_option('-H', '--mysql-host', dest='mysql_host', type='string',
        help='MySQL server hostname/IP address')
    parser.add_option('-P', '--mysql-port', dest='mysql_port', type='int',
        help='MySQL server port, socket has precendence')
    parser.add_option('-S', '--mysql-socket', dest='mysql_sock', type='string',
        help='MySQL server path to socket file')
    parser.add_option('-c', '--mysql-cnf', dest='mysql_cnf', type='string',
        help=('Path to custom my.cnf, in case you want to pass this value to '
            'innobackupex --defaults-file'))
    parser.add_option('-s', '--stor-dir', dest='stor_dir', type='string',
        help='Path to directory where backups are stored.')
    parser.add_option('-w', '--work-dir', dest='work_dir', type='string',
        help='Path to temporary backup work directory')
    parser.add_option('-b', '--retention-binlogs', dest='retention_binlogs', type="int",
        help='Binary log period retention, in days')
    parser.add_option('', '--extra-ibx-options', dest='extra_ibx_options', type='string',
        help=('Specify additional innobackupex options, make sure to '
            'mind your quotes and avoid conflicts with --encrypt*, '
            '--compress, --remote-host - will think of better way to '
            'handle this in the future!'))
    parser.add_option('-z', '--compress', dest='compress',  action="store_true",
        help='Compress backups, by default with gzip, see -Z')
    parser.add_option('-Z', '--compress-with', dest='compress_with',
        help='Compress backup with binary, default gzip, options (gzip, qpress)')
    parser.add_option('-M', '--notify-by-email', dest='notify_by_email',
        help='Send failed backup notifications to this address(es)')
    parser.add_option('', '--notify-on-success', dest='notify_on_success',
        help='Send success backup notifications to this address(es)')
    parser.add_option('-R', '--remote-stor-dir', dest='remote_stor_dir',
        help=('When --remote-host is not empty, backups to that host will be '
            'streamed to this directory, similar to --stor-dir'))
    parser.add_option('-T', '--remote-host', dest='remote_host',
        help='Stream backups to this remote host')
    parser.add_option('-L', '--remote-push-only', dest='remote_push_only', action="store_true",
        help=('Instructs xtrabackup that all backups will be pushed to '
            'remote only, no local post processing'))
    parser.add_option('-B', '--remote-script', dest='remote_script',
        help=('When --remote-push-only is enabled, we need to specify the '
            'path to this script on the remote server, default is xbackup.py'))
    parser.add_option('', '--remote-nc-port', dest='remote_nc_port',
        help=('When requesting to open a netcat port, this is the port number '
            'to try with, can be a range separated with comma'))
    parser.add_option('-C', '--ssh-opts', dest='ssh_opts',
        help=('SSH options when streaming backups to remote host '
            'i.e. -i /path/to/identity file'))
    parser.add_option('-U', '--ssh-user', dest='ssh_user',
        help='SSH account to user when streaming backups to remote host, default is root')
    parser.add_option('-x', '--apply-log', dest='apply_log', action="store_true",
        help='Verify backups with --apply-log, requires enough disk space on --workdir')
    parser.add_option('-m', '--prepare-memory', dest='prepare_memory', type="int",
        help='How much memory to use with innobackupex --use-memory in MB, default 128M')
    parser.add_option('-o', '--status-format', dest='status_format', type="string",
        help=('For status command, what output format, default=none, '
            'possible values: none, nagios, zabbix (cli)'))
    parser.add_option('-r', '--restore-backup', dest='restore_backup', type="string",
        help=('With command restore-set, specify which backup to restore, '
            'choose any from output of list command. Default is restore to last '
            'successful backup. (cli)'))
    parser.add_option('-e', '--restore-dir', dest='restore_dir', type="string",
        help='With command restore, specify where to restore selected backup (cli)')
    parser.add_option('-i', '--retention-sets', dest='retention_sets',
        help='How many sets of combined full + incr to keep on storage, default 2')
    parser.add_option('-j', '--retention-months', dest='retention_months', type="int",
        help='How many rotated monthly backups to keep, default 0',
        default=0)
    parser.add_option('-k', '--retention-weeks', dest='retention_weeks', type="int",
        help='How many rotated weekly backups to keep, default 0',
        default=0)
    parser.add_option('-t', '--meta-item', dest='meta_item', type="string",
        help=('Query meta information about backups, used when backups '
            'are push to remote location. Allows the script to query information '
            'about backups stored remotely'))
    parser.add_option('-n', '--first-binlog', dest='first_binlog', type="string",
        help=('For binlog-stream, if the script cannot determine the oldest '
            'binary log filename from the backups to maintain the list of files '
            'to keep, we can specify it manually here'))
    parser.add_option('', '--binlog-from-master', dest='binlog_from_master', action="store_true",
        help=('For binlog-stream, when --slave-info is enabled on the backups '
            'and you want to stream binary logs from the master instead '
            'this tells the script to determine the correct binary log file name'))
    parser.add_option('-l', '--binlog-binary', dest='binlog_binary', type="string",
        help=('For binlog-stream, specify where the 5.6+ mysqlbinlog utility '
            'is located'))
    parser.add_option('-d', '--debug', dest='debug', action="store_true",
        help='Enable debugging, more verbose output (cli)',
        default=False)
    parser.add_option('-q', '--quiet', dest='quiet', action="store_true",
        help='Supress all messages errors except intended output i.e. list command (cli)',
        default=False)
    parser.add_option('-X', '--i-am-absolutely-sure-wipeout', dest='wipeout', action="store_true",
        help='Confirm to **WIPEOUT** all backups with wipeout command! (cli)',
        default=False)
    parser.add_option('', '--encrypt', dest='encrypt', type="string",
        help='Whether to encrypt backups on storage')
    parser.add_option('', '--encrypt-key-file', dest='encrypt_key_file', type="string",
        help=('Key file for encrypting/decrypting backups'))
    parser.add_option('', '--purge-bitmaps', dest='purge_bitmaps',  action="store_true",
        help=('If Changed Page Tracking is enabled, should we automatically '
            'purge bitmaps? Requires that a valid mysql-user and mysql-pass ' 
            'with SUPER privieleges is specified.'))

    (options, args) = parser.parse_args()

    if options.debug: xb_opt_debug = True
    if options.quiet: xb_opt_quiet = True
    if options.wipeout: xb_opt_wipeout = True

    if xb_opt_quiet and xb_opt_debug:
        _die("--debug and --quiet are mutually exclusive")

    if options.config:
        xb_opt_config = os.path.realpath(options.config)
        if not os.path.isfile(xb_opt_config):
            _die("The specified configuration file %s " % options.config,
                "does not exist or is not readable!")
        else:
            _say("Using config file %s" % xb_opt_config)

    if options.config_section: xb_opt_config_section = options.config_section

    if os.path.isfile(xb_opt_config):
        xb_cfg = ConfigParser()
        xb_cfg.read(xb_opt_config)

        if xb_cfg.has_option(xb_opt_config_section, 'mysql_host'):
            xb_opt_mysql_host = xb_cfg.get(xb_opt_config_section, 'mysql_host')

        if xb_cfg.has_option(xb_opt_config_section, 'mysql_user'):
            xb_opt_mysql_user = xb_cfg.get(xb_opt_config_section, 'mysql_user')

        if xb_cfg.has_option(xb_opt_config_section, 'mysql_pass'):
            xb_opt_mysql_pass = xb_cfg.get(xb_opt_config_section, 'mysql_pass')

        if xb_cfg.has_option(xb_opt_config_section, 'mysql_port'):
            xb_opt_mysql_port = int(xb_cfg.get(xb_opt_config_section, 'mysql_port'))

        if xb_cfg.has_option(xb_opt_config_section, 'mysql_sock'):
            xb_opt_mysql_sock = xb_cfg.get(xb_opt_config_section, 'mysql_sock')

        if xb_cfg.has_option(xb_opt_config_section, 'mysql_cnf'):
            xb_opt_mysql_cnf = xb_cfg.get(xb_opt_config_section, 'mysql_cnf')

        if xb_cfg.has_option(xb_opt_config_section, 'stor_dir'):
            xb_opt_stor_dir = xb_cfg.get(xb_opt_config_section, 'stor_dir').rstrip('/')

        if xb_cfg.has_option(xb_opt_config_section, 'work_dir'):
            xb_opt_work_dir = xb_cfg.get(xb_opt_config_section, 'work_dir').rstrip('/')

        if xb_cfg.has_option(xb_opt_config_section, 'ssh_opts'):
            xb_opt_ssh_opts = xb_cfg.get(xb_opt_config_section, 'ssh_opts')

        if xb_cfg.has_option(xb_opt_config_section, 'ssh_user'):
            xb_opt_ssh_user = xb_cfg.get(xb_opt_config_section, 'ssh_user')

        if xb_cfg.has_option(xb_opt_config_section, 'remote_stor_dir'):
            xb_opt_remote_stor_dir = xb_cfg.get(xb_opt_config_section, 'remote_stor_dir').rstrip('/')

        if xb_cfg.has_option(xb_opt_config_section, 'remote_host'):
            xb_opt_remote_host = xb_cfg.get(xb_opt_config_section, 'remote_host')

        if xb_cfg.has_option(xb_opt_config_section, 'remote_script'):
            xb_opt_remote_script = xb_cfg.get(xb_opt_config_section, 'remote_script')

        if xb_cfg.has_option(xb_opt_config_section, 'remote_push_only'):
            xb_opt_remote_push_only = bool(int(xb_cfg.get(xb_opt_config_section, 'remote_push_only')))

        if xb_cfg.has_option(xb_opt_config_section, 'remote_nc_port'):
            if not _parse_port_param(xb_cfg.get(xb_opt_config_section, 'remote_nc_port')):
                parser.error("The specified port (range) is not valid")
            else:
                xb_opt_remote_nc_port = xb_cfg.get(xb_opt_config_section, 'remote_nc_port')

        if xb_cfg.has_option(xb_opt_config_section, 'retention_binlogs'):
            xb_opt_retention_binlogs = int(xb_cfg.get(xb_opt_config_section, 'retention_binlogs'))

        if xb_cfg.has_option(xb_opt_config_section, 'binlog_binary'):
            xb_opt_binlog_binary = xb_cfg.get(xb_opt_config_section, 'binlog_binary')

        if xb_cfg.has_option(xb_opt_config_section, 'binlog_from_master'):
            xb_opt_binlog_from_master = xb_cfg.get(xb_opt_config_section, 'binlog_from_master')

        if xb_cfg.has_option(xb_opt_config_section, 'compress'):
            xb_opt_compress = bool(int(xb_cfg.get(xb_opt_config_section, 'compress')))

        if xb_cfg.has_option(xb_opt_config_section, 'compress_with'):
            xb_opt_compress_with = xb_cfg.get(xb_opt_config_section, 'compress_with')

        if xb_cfg.has_option(xb_opt_config_section, 'notify_by_email'):
            xb_opt_notify_by_email = xb_cfg.get(xb_opt_config_section, 'notify_by_email')

        if xb_cfg.has_option(xb_opt_config_section, 'notify_on_success'):
            xb_opt_notify_on_success = xb_cfg.get(xb_opt_config_section, 'notify_on_success')

        if xb_cfg.has_option(xb_opt_config_section, 'apply_log'):
            xb_opt_apply_log = bool(int(xb_cfg.get(xb_opt_config_section, 'apply_log')))

        if xb_cfg.has_option(xb_opt_config_section, 'prepare_memory'):
            xb_opt_prepare_memory = int(xb_cfg.get(xb_opt_config_section, 'prepare_memory'))

        if xb_cfg.has_option(xb_opt_config_section, 'retention_sets'):
            if int(xb_cfg.get(xb_opt_config_section, 'retention_sets')) > 0:
                xb_opt_retention_sets = int(xb_cfg.get(xb_opt_config_section, 'retention_sets'))

        if xb_cfg.has_option(xb_opt_config_section, 'retention_months'):
            if int(xb_cfg.get(xb_opt_config_section, 'retention_months')) > 0:
                xb_opt_retention_months = int(xb_cfg.get(xb_opt_config_section, 'retention_months'))

        if xb_cfg.has_option(xb_opt_config_section, 'retention_weeks'):
            if int(xb_cfg.get(xb_opt_config_section, 'retention_weeks')) > 0:
                xb_opt_retention_weeks = int(xb_cfg.get(xb_opt_config_section, 'retention_weeks'))

        if xb_cfg.has_option(xb_opt_config_section, 'encrypt_key_file'):
            xb_opt_encrypt_key_file = xb_cfg.get(xb_opt_config_section, 'encrypt_key_file')

        if xb_cfg.has_option(xb_opt_config_section, 'encrypt'):
            xb_opt_encrypt = xb_cfg.get(xb_opt_config_section, 'encrypt')

        if xb_cfg.has_option(xb_opt_config_section, 'extra_ibx_options'):
            xb_opt_extra_ibx_options = xb_cfg.get(xb_opt_config_section, 'extra_ibx_options')

        if xb_cfg.has_option(xb_opt_config_section, 'purge_bitmaps'):
            xb_opt_purge_bitmaps = xb_cfg.get(xb_opt_config_section, 'purge_bitmaps')

    if options.mysql_user: xb_opt_mysql_user = options.mysql_user
    if options.mysql_pass: xb_opt_mysql_pass = options.mysql_pass
    if options.mysql_host: xb_opt_mysql_host = options.mysql_host
    if options.mysql_port: xb_opt_mysql_port = options.mysql_port
    if options.mysql_sock: xb_opt_mysql_sock = options.mysql_sock
    if options.mysql_cnf: xb_opt_mysql_cnf = options.mysql_cnf
    if options.stor_dir: xb_opt_stor_dir = options.stor_dir.rstrip('/')
    if options.work_dir: xb_opt_work_dir = options.work_dir.rstrip('/')
    if options.retention_binlogs: xb_opt_retention_binlogs = options.retention_binlogs
    if options.compress: xb_opt_compress = options.compress
    if options.compress_with: xb_opt_compress_with = options.compress_with
    if options.notify_by_email: xb_opt_notify_by_email = options.notify_by_email
    if options.notify_on_success: xb_opt_notify_on_success = options.notify_on_success
    if options.first_binlog: xb_opt_first_binlog = options.first_binlog
    if options.binlog_binary: xb_opt_binlog_binary = options.binlog_binary
    if options.binlog_from_master: xb_opt_binlog_from_master = options.binlog_from_master

    if options.remote_stor_dir: xb_opt_remote_stor_dir = options.remote_stor_dir
    if options.remote_host: xb_opt_remote_host = options.remote_host
    if options.remote_script: xb_opt_remote_script = options.remote_script
    if options.remote_push_only is not None:
        xb_opt_remote_push_only = options.remote_push_only
    
    if options.remote_nc_port is not None and \
            not _parse_port_param(options.remote_nc_port):
        parser.error("The specified port (range) is not valid")
    else:
        xb_opt_remote_nc_port = options.remote_nc_port

    if options.ssh_opts: xb_opt_ssh_opts = options.ssh_opts
    if options.ssh_user: xb_opt_ssh_user = options.ssh_user
    if options.meta_item: xb_opt_meta_item = options.meta_item

    if xb_opt_remote_host is not None and xb_opt_remote_stor_dir is None:
        parser.error("Remote host specified but, remote store directory is empty")

    if options.apply_log: xb_opt_apply_log = options.apply_log
    if options.prepare_memory: xb_opt_prepare_memory = options.prepare_memory
    if options.status_format: xb_opt_status_format = options.status_format
    if options.restore_backup is not None:
        xb_opt_restore_backup = options.restore_backup
    if options.restore_dir is not None:
        xb_opt_restore_dir = options.restore_dir
    if options.retention_sets and int(options.retention_sets) > 0:
        xb_opt_retention_sets = int(options.retention_sets)
    if options.retention_months > 0:
        xb_opt_retention_months = int(options.retention_months)
    if options.retention_weeks > 0:
        xb_opt_retention_weeks = int(options.retention_weeks)

    if options.encrypt: xb_opt_encrypt = options.encrypt
    if options.encrypt_key_file: xb_opt_encrypt_key_file = options.encrypt_key_file
    if options.extra_ibx_options: xb_opt_extra_ibx_options = options.extra_ibx_options
    if options.purge_bitmaps: xb_opt_purge_bitmaps = options.purge_bitmaps

    if xb_cfg: _debug('Found config file: ', xb_opt_config)

    cmds = [XB_CMD_FULL, XB_CMD_INCR, XB_CMD_LIST, XB_CMD_STAT, XB_CMD_PREP,
            XB_CMD_APPL, XB_CMD_PRUNE, XB_CMD_META, XB_CMD_BINLOGS, XB_CMD_WIPE]
    if len(args) >= 1 and args[0] not in cmds:
        parser.error("Command not recognized, got '%s'. See more with --help" % args[0])
    elif len(args) <= 0:
        parser.error("Command not specified. See more with --help")
    else:
        xb_opt_command = args[0]

    if xb_opt_remote_push_only and xb_opt_apply_log:
        _die("--remote-push-only and --apply-log are mutually exclusive")

    if options.retention_sets is not None and options.retention_sets <= 0:
        _die("Invalid value for retention sets, ",
            "you should keep one or more backup sets!")

    if xb_opt_encrypt and not os.path.isfile(xb_opt_encrypt_key_file):
        _die("The specified key file does not exist!")

    if xb_opt_encrypt and xb_opt_compress and xb_opt_compress_with == 'gzip':
        _die("GZIP compression + encryption is not supported ",
            "at the moment. Please use --compress-with=qpress instead.")

    if xb_opt_encrypt and not xb_opt_compress:
        _die("Encryption requires compression for now, support for ",
            "uncompressed encrypted backup will be added in the future")

    if xb_opt_command in [XB_CMD_FULL, XB_CMD_INCR, XB_CMD_PREP, XB_CMD_APPL]:
        _check_binary('innobackupex')
        _check_binary('xtrabackup')

    if xb_opt_remote_nc_port_min:
        _check_binary('nc')
        _check_binary('netstat')

    if xb_opt_encrypt or xb_opt_encrypt_key_file:
        _check_binary('xbcrypt')

    if xb_opt_compress:
        _check_binary('xbstream')

    if xb_opt_compress_with == 'qpress':
        _check_binary('qpress')

    # store xtrabackup version numbers
    _xb_version()

    # we test email delivery beforehand to make sure it works
    # this will happen only once as long as the sentinel file exists
    # i.e. STOR_DIR/pyxbackup_mail_ok
    mail_status_file = "%s/%s_mail_ok" % (xb_opt_stor_dir, XB_BIN_NAME)
    if (xb_opt_notify_by_email or xb_opt_notify_on_success) and \
            not os.path.isfile(mail_status_file):
        mail_message = "This is a test message from %s@%s, please ignore." % (
            xb_user, xb_hostname)
        mail_subject = "pyxbackup Test Mail"
        mail_to = xb_opt_notify_by_email \
            if xb_opt_notify_by_email else xb_opt_notify_on_success

        _say("Mail has not been tested, sending initial test mail.")

        if _notify_by_email(mail_subject, mail_message, mail_to):
            open(mail_status_file, 'a').close()

    if xb_opt_debug:
        _debug("Supplied options:")
        for x, v in options.__dict__.items():
            _debug(("\t%s: %s" % (x, globals()['xb_opt_' + str(x)])))
        _debug("\tcommand: %s" % xb_opt_command)

def check_dirs():
    """Check and create required directories if they do not exist yet"""

    global xb_stor_full
    global xb_stor_incr
    global xb_stor_weekly
    global xb_stor_monthly
    global xb_stor_binlogs

    if not os.path.isdir(xb_opt_stor_dir):
        _die("The store directory \"%s\" is not a valid directory" % xb_opt_stor_dir)

    if not os.path.isdir(xb_opt_work_dir):
        _die("The work directory \"%s\" is not a valid directory" % xb_opt_work_dir)

    xb_stor_full = xb_opt_stor_dir + '/full'
    xb_stor_incr = xb_opt_stor_dir + '/incr'
    xb_stor_weekly = xb_opt_stor_dir + '/weekly'
    xb_stor_monthly = xb_opt_stor_dir + '/monthly'
    xb_stor_binlogs = xb_opt_stor_dir + '/binlogs'

    if not os.path.isdir(xb_stor_full): os.mkdir(xb_stor_full, 0755)
    if not os.path.isdir(xb_stor_incr): os.mkdir(xb_stor_incr, 0755)
    if not os.path.isdir(xb_stor_weekly): os.mkdir(xb_stor_weekly, 0755)
    if not os.path.isdir(xb_stor_monthly): os.mkdir(xb_stor_monthly, 0755)
    if not os.path.isdir(xb_stor_binlogs): os.mkdir(xb_stor_binlogs, 0755)

def list_backups():
    """List all valid backups inside the store directory"""

    global xb_last_full
    global xb_last_incr
    global xb_full_list
    global xb_incr_list
    global xb_weekly_list
    global xb_monthly_list
    global xb_last_backup
    global xb_last_backup_is

    l = os.listdir(xb_stor_full)
    if len(l) <= 0 and xb_opt_command == XB_CMD_INCR and not xb_opt_remote_push_only:
        _exit_code(XB_EXIT_NO_FULL)
        _die("There is no available full backup for incremental from ",
            xb_stor_full)

    l.sort()
    l.reverse()
    xb_full_list = []
    unrecognized_backups = False

    for d in l:
        _debug("Checking full directory ", os.path.join(xb_stor_full, d))
        if os.path.isfile(os.path.join(xb_stor_full, d)):
            _say(os.path.join(xb_stor_full, d), " is not recognized as backup")
            unrecognized_backups = True
            continue

        if not os.path.isfile(os.path.join(xb_stor_full, d, XB_TAG_FILE)):
            _debug("Full backup ", os.path.join(xb_stor_full, d),
                " is not recognized as full")
            unrecognized_backups = True
            continue

        if not xb_last_full: xb_last_full = d
        xb_full_list.append(d)

    xb_incr_list = dict()

    l = os.listdir(xb_stor_incr)
    if len(l) > 0:
        for d in xb_full_list:
            if not os.path.isdir(os.path.join(xb_stor_incr, d)):
                continue

            i = os.listdir(os.path.join(xb_stor_incr, d))
            if len(i) <= 0:
                xb_incr_list[d] = None
                continue
            else:
                i.sort()
                i.reverse()

            if d not in xb_full_list:
                _debug("A group of incremental backup from the folder ",
                    os.path.join(xb_stor_incr, d), " has no parent backup from ",
                    xb_stor_full)

            # We iterate over a copy of the list, otherwise we lose reference
            # to the list in case the first condition is hit i.e. invalid backup
            for r in i[:]:
                if not os.path.isfile(os.path.join(xb_stor_incr, d, r, XB_TAG_FILE)):
                    _debug("Incremental backup ", os.path.join(xb_stor_incr, d, r),
                        " is not recognized as incremental")
                    unrecognized_backups = True
                    i.remove(r)
                elif d == xb_last_full and xb_last_incr is None:
                    _debug('I never hit this one!')
                    xb_last_incr = r
                    xb_last_backup = xb_last_incr
                    xb_last_backup_is = XB_CMD_INCR

            xb_incr_list[d] = i


    if xb_last_backup is None:
        xb_last_backup = xb_last_full
        xb_last_backup_is = XB_CMD_FULL

    _debug("Full list: ", str(xb_full_list))
    _debug("Last full: ", xb_last_full)
    if xb_last_incr:
        _debug("Incr list: ", str(xb_incr_list))
        _debug("Last incr: ", xb_last_incr)

    l = os.listdir(xb_stor_weekly)
    if len(l) > 0:
        for d in l:
            if not os.path.isdir(os.path.join(xb_stor_weekly, d)):
                _debug("%s is not recognized as backup" % d)
                unrecognized_backups = True
                continue

            if not os.path.isdir(os.path.join(xb_stor_weekly, d, 'full')):
                _debug("%s is not recognized as weekly backup" % d)
                unrecognized_backups = True
                continue

            if xb_weekly_list is None: xb_weekly_list = []
            xb_weekly_list.append(d)

    _debug("Weekly list: %s" % str(xb_weekly_list))

    l = os.listdir(xb_stor_monthly)
    if len(l) > 0:
        for d in l:
            if not os.path.isdir(os.path.join(xb_stor_monthly, d)):
                _debug("%s is not recognized as backup" % d)
                unrecognized_backups = True
                continue

            if not os.path.isdir(os.path.join(xb_stor_monthly, d, 'full')):
                _debug("%s is not recognized as monthly backup" % d)
                unrecognized_backups = True
                continue

            if xb_monthly_list is None: xb_monthly_list = []
            xb_monthly_list.append(d)

    _debug("Monthly list: %s" % str(xb_monthly_list))

    list_binlogs()

    if unrecognized_backups == True:
        _warn("Some files inside %s were not recognized " % xb_opt_stor_dir,
            "as either complete or an actual backup directory.")
        if xb_opt_debug:
            _warn("Please review the files/folders above that are marked ",
                "**not recognized**")
        else:
            _warn("To get a list of these files, please run the list command ",
                "with --debug option specified.")
        _warn("If these files are not needed, you can remove them from the ",
            "filesystem to free up some disk space safely.")

def list_binlogs():
    global xb_first_binlog
    global xb_last_binlog
    global xb_binlogs_list
    global xb_binlog_name

    xb_binlogs_list = None

    l = os.listdir(xb_stor_binlogs)
    if len(l) > 0:
        for d in l:
            f = os.path.join(xb_stor_binlogs, d)
            if not os.path.isfile(f):
                _debug("%s is not a file, skipping" % d)
                continue

            # skip the magic number check if the name matches
            # sort of an optimization to skip opening each file
            # if you have thousands of binary logs
            if xb_binlog_name and xb_binlog_name == d[0:-7]:
                _debug("%s matches binary log name, appending" % d)
            # we check the magic number for the binary log to validate
            elif open(f, 'rb').read(4) != '\xfebin':
                _debug("%s is not a valid binary log, skipping" % d)
                continue
            elif xb_binlog_name is None:
                xb_binlog_name = d[0:-7]

            if xb_binlogs_list is None: xb_binlogs_list = []
            xb_binlogs_list.append(d)

    if xb_binlogs_list is not None:
        xb_binlogs_list.sort()
        _debug("Binary logs list: %s" % str(xb_binlogs_list))
        xb_first_binlog = xb_binlogs_list[0]
        xb_last_binlog = xb_binlogs_list[len(xb_binlogs_list)-1]

# http://stackoverflow.com/questions/1857346/\
# python-optparse-how-to-include-additional-info-in-usage-output
class PyxOptParser(OptionParser):
    def format_epilog(self, formatter):
        return self.epilog

if __name__ == "__main__":
    try:
        signal.signal(signal.SIGTERM, _sigterm_handler)
        xb_curdate = date(time.time(), '%Y_%m_%d-%H_%M_%S')
        xb_cwd = os.path.dirname(os.path.realpath(__file__))
        xb_hostname = os.uname()[1]
        xb_user = pwd.getpwuid(os.getuid())[0]

        dt = datetime.strptime(xb_curdate, '%Y_%m_%d-%H_%M_%S')
        if dt.weekday() == 6:
            xb_is_last_day_of_week = True

        if calendar.monthrange(dt.year, dt.month)[1] == dt.day:
            xb_is_last_day_of_month = True

        init()
        check_dirs()
        if xb_opt_command not in cmd_no_log:
            # Initially our log file is created in /tmp/ until we can validate and
            # make sure we can write to xb_opt_work_dir
            _init_log_file("%s/%s-%s" % (xb_opt_work_dir, xb_curdate, XB_LOG_NAME))

        list_backups()
        os.chdir(xb_opt_work_dir)

        XB_LCK_FILE = os.path.join(xb_opt_work_dir, "%s.lock" % XB_BIN_NAME)
        if not _check_in_progress():
            _create_lock_file()

        if xb_opt_command == XB_CMD_FULL:
            run_xb_full()
        elif xb_opt_command == XB_CMD_INCR:
            run_xb_incr()
        elif xb_opt_command == XB_CMD_LIST:
            run_xb_list()
        elif xb_opt_command == XB_CMD_PREP:
            run_xb_restore_set()
        elif xb_opt_command == XB_CMD_APPL:
            run_xb_apply_last()
        elif xb_opt_command == XB_CMD_PRUNE:
            prune_full_incr()
            prune_weekly()
            prune_monthly()
        elif xb_opt_command == XB_CMD_META:
            run_meta_query()
        elif xb_opt_command == XB_CMD_BINLOGS:
            run_binlog_stream()
        elif xb_opt_command == XB_CMD_WIPE:
            run_wipeout()
        else: run_status()

        _destroy_lock_file()

        if os.path.isfile(xb_log_file):
            if xb_opt_remote_host and xb_opt_command not in [XB_CMD_PREP, XB_CMD_APPL]:
                _push_to_remote_scp(xb_log_file, "%s/" % xb_this_backup_remote.rstrip('/'))
            _init_log_file(xb_log_file, True)

        if xb_log_fd is not None:
            os.close(xb_log_fd)

        if xb_exit_code > 0 and xb_opt_notify_by_email:
            _notify_by_email("MySQL backup script at %s has errors!" % xb_hostname)
        elif xb_opt_notify_on_success and xb_opt_command in [XB_CMD_FULL, XB_CMD_INCR]:
            _notify_by_email(
                "MySQL backup script at %s completed successfully!" % xb_hostname,
                xb_backup_summary, xb_opt_notify_on_success)

        sys.exit(xb_exit_code)
    except Exception, e:
        if xb_opt_notify_by_email:
            _notify_by_email(
                "MySQL backup script at %s exception!" % xb_hostname,
                traceback.format_exc())

        if xb_exit_code > 0:
            sys.exit(xb_exit_code)
            if xb_opt_debug: traceback.print_exc()
        else:
            _error("An uncaught exception error has occurred!")
            traceback.print_exc()

        sys.exit(255)

class PyxOptions(object):
    config = None
    config_section = None
    stor_dir = ''
    work_dir = ''
    mysql_user = None
    mysql_pass = None
    mysql_host = 'localhost'
    mysql_port = 3306
    mysql_sock = '/tmp/mysql.sock'
    mysql_cnf = None
    retention_binlogs = False
    compress = False
    compress_with = 'gzip'
    apply_log = False
    prepare_memory = 128
    retention_sets = 2
    retention_months = 0
    retention_weeks = 0
    debug = False
    quiet = False
    status_format = None
    command = 'status'
    restore_backup = None
    restore_dir = None
    remote_stor_dir = None
    remote_host = None
    remote_push_only = None
    remote_script = XB_BIN_NAME
    remote_nc_port = 0
    remote_nc_port_min = 0
    remote_nc_port_max = 0
    ssh_opts = ''
    ssh_user = None
    notify_by_email = None
    notify_on_success = None
    meta_item = None
    wipeout = False
    first_binlog = False
    binlog_binary = None
    binlog_from_master = False
    encrypt = False
    encrypt_key_file = None
    extra_ibx_options = None
    purge_bitmaps = None
    
    def __init__(self):

        _init_log_file("/tmp/%s-%s" % (xb_curdate, XB_LOG_NAME))

        p_usage = "Usage: %prog [options] COMMAND"
        p_desc = "Managed xtrabackup based backups."
        p_epilog = ["\n"
            "Options here can also be specified on a filed called %s.cnf \n"
            "which will be checked in this order: \n\n"
            "- on a file specified via the --config option\n"
            "- /etc/pyxbackup.cnf\n"
            "- on the same directory of the script \n\n"
            "Valid commands are:\n\n"
            "\tfull: Execute full backups\n"
            "\tincr: Execute incremental backups\n"
            "\tlist: List existing backups and additional information\n"
            "\tstatus: Check status of last backup\n"
            "\tapply-last: Prepare to the most recent backup\n"
            "\trestore-set: Restore to a specific backup set\n"
            "\tlast-lsn: Print out to_lsn value of last backup for incremental use\n"
            "\twipeout: Cleanup all existing backups\n"]
        p_epilog = p_epilog % XB_BIN_NAME

        parser = PyxOptParser(p_usage, version="%prog " + str(xb_version),
            description=p_desc, epilog=p_epilog)
        parser.add_option('-f', '--config', dest='config', type='string',
            help='Path to config file to use, useful for multiple back locations')
        parser.add_option('', '--config-section', dest='config_section', type='string',
            help=('By default, config options are read from the %s section. '
                'If you have multiple sections/profile in the configuration file '
                'you can specify the section name, similar to mysql --defaults-group. '
                '(cli)')  % XB_BIN_NAME)
        parser.add_option('-u', '--mysql-user', dest='mysql_user', type='string',
            help='MySQL server username')
        parser.add_option('-p', '--mysql-pass', dest='mysql_pass', type='string',
            help='MySQL server password')
        parser.add_option('-H', '--mysql-host', dest='mysql_host', type='string',
            help='MySQL server hostname/IP address')
        parser.add_option('-P', '--mysql-port', dest='mysql_port', type='int',
            help='MySQL server port, socket has precendence')
        parser.add_option('-S', '--mysql-socket', dest='mysql_sock', type='string',
            help='MySQL server path to socket file')
        parser.add_option('-c', '--mysql-cnf', dest='mysql_cnf', type='string',
            help=('Path to custom my.cnf, in case you want to pass this value to '
                'innobackupex --defaults-file'))
        parser.add_option('-s', '--stor-dir', dest='stor_dir', type='string',
            help='Path to directory where backups are stored.')
        parser.add_option('-w', '--work-dir', dest='work_dir', type='string',
            help='Path to temporary backup work directory')
        parser.add_option('-b', '--retention-binlogs', dest='retention_binlogs', type="int",
            help='Binary log period retention, in days')
        parser.add_option('', '--extra-ibx-options', dest='extra_ibx_options', type='string',
            help=('Specify additional innobackupex options, make sure to '
                'mind your quotes and avoid conflicts with --encrypt*, '
                '--compress, --remote-host - will think of better way to '
                'handle this in the future!'))
        parser.add_option('-z', '--compress', dest='compress',  action="store_true",
            help='Compress backups, by default with gzip, see -Z')
        parser.add_option('-Z', '--compress-with', dest='compress_with',
            help='Compress backup with binary, default gzip, options (gzip, qpress)')
        parser.add_option('-M', '--notify-by-email', dest='notify_by_email',
            help='Send failed backup notifications to this address(es)')
        parser.add_option('', '--notify-on-success', dest='notify_on_success',
            help='Send success backup notifications to this address(es)')
        parser.add_option('-R', '--remote-stor-dir', dest='remote_stor_dir',
            help=('When --remote-host is not empty, backups to that host will be '
                'streamed to this directory, similar to --stor-dir'))
        parser.add_option('-T', '--remote-host', dest='remote_host',
            help='Stream backups to this remote host')
        parser.add_option('-L', '--remote-push-only', dest='remote_push_only', action="store_true",
            help=('Instructs xtrabackup that all backups will be pushed to '
                'remote only, no local post processing'))
        parser.add_option('-B', '--remote-script', dest='remote_script',
            help=('When --remote-push-only is enabled, we need to specify the '
                'path to this script on the remote server, default is xbackup.py'))
        parser.add_option('', '--remote-nc-port', dest='remote_nc_port',
            help=('When requesting to open a netcat port, this is the port number '
                'to try with, can be a range separated with comma'))
        parser.add_option('-C', '--ssh-opts', dest='ssh_opts',
            help=('SSH options when streaming backups to remote host '
                'i.e. -i /path/to/identity file'))
        parser.add_option('-U', '--ssh-user', dest='ssh_user',
            help='SSH account to user when streaming backups to remote host, default is root')
        parser.add_option('-x', '--apply-log', dest='apply_log', action="store_true",
            help='Verify backups with --apply-log, requires enough disk space on --workdir')
        parser.add_option('-m', '--prepare-memory', dest='prepare_memory', type="int",
            help='How much memory to use with innobackupex --use-memory in MB, default 128M')
        parser.add_option('-o', '--status-format', dest='status_format', type="string",
            help=('For status command, what output format, default=none, '
                'possible values: none, nagios, zabbix (cli)'))
        parser.add_option('-r', '--restore-backup', dest='restore_backup', type="string",
            help=('With command restore-set, specify which backup to restore, '
                'choose any from output of list command. Default is restore to last '
                'successful backup. (cli)'))
        parser.add_option('-e', '--restore-dir', dest='restore_dir', type="string",
            help='With command restore, specify where to restore selected backup (cli)')
        parser.add_option('-i', '--retention-sets', dest='retention_sets',
            help='How many sets of combined full + incr to keep on storage, default 2')
        parser.add_option('-j', '--retention-months', dest='retention_months', type="int",
            help='How many rotated monthly backups to keep, default 0',
            default=0)
        parser.add_option('-k', '--retention-weeks', dest='retention_weeks', type="int",
            help='How many rotated weekly backups to keep, default 0',
            default=0)
        parser.add_option('-t', '--meta-item', dest='meta_item', type="string",
            help=('Query meta information about backups, used when backups '
                'are push to remote location. Allows the script to query information '
                'about backups stored remotely'))
        parser.add_option('-n', '--first-binlog', dest='first_binlog', type="string",
            help=('For binlog-stream, if the script cannot determine the oldest '
                'binary log filename from the backups to maintain the list of files '
                'to keep, we can specify it manually here'))
        parser.add_option('', '--binlog-from-master', dest='binlog_from_master', action="store_true",
            help=('For binlog-stream, when --slave-info is enabled on the backups '
                'and you want to stream binary logs from the master instead '
                'this tells the script to determine the correct binary log file name'))
        parser.add_option('-l', '--binlog-binary', dest='binlog_binary', type="string",
            help=('For binlog-stream, specify where the 5.6+ mysqlbinlog utility '
                'is located'))
        parser.add_option('-d', '--debug', dest='debug', action="store_true",
            help='Enable debugging, more verbose output (cli)',
            default=False)
        parser.add_option('-q', '--quiet', dest='quiet', action="store_true",
            help='Supress all messages errors except intended output i.e. list command (cli)',
            default=False)
        parser.add_option('-X', '--i-am-absolutely-sure-wipeout', dest='wipeout', action="store_true",
            help='Confirm to **WIPEOUT** all backups with wipeout command! (cli)',
            default=False)
        parser.add_option('', '--encrypt', dest='encrypt', type="string",
            help='Whether to encrypt backups on storage')
        parser.add_option('', '--encrypt-key-file', dest='encrypt_key_file', type="string",
            help=('Key file for encrypting/decrypting backups'))
        parser.add_option('', '--purge-bitmaps', dest='purge_bitmaps',  action="store_true",
            help=('If Changed Page Tracking is enabled, should we automatically '
                'purge bitmaps? Requires that a valid mysql-user and mysql-pass ' 
                'with SUPER privieleges is specified.'))

        (options, args) = parser.parse_args()

        if options.debug: debug = True
        if options.quiet: quiet = True
        if options.wipeout: wipeout = True

        if quiet and debug:
            _die("--debug and --quiet are mutually exclusive")

        config = "/etc/%s.cnf" % XB_BIN_NAME
        if not os.path.isfile(config):
            config = "%s/%s.cnf" % (xb_cwd, XB_BIN_NAME)
        config_section = XB_BIN_NAME

        if options.config:
            config = os.path.realpath(options.config)
            if not os.path.isfile(config):
                _die("The specified configuration file %s " % options.config,
                    "does not exist or is not readable!")
            else:
                _say("Using config file %s" % config)

        if options.config_section: config_section = options.config_section
        cfg = self.read_config_file(config, config_section)

        if options.mysql_user: mysql_user = options.mysql_user
        if options.mysql_pass: mysql_pass = options.mysql_pass
        if options.mysql_host: mysql_host = options.mysql_host
        if options.mysql_port: mysql_port = options.mysql_port
        if options.mysql_sock: mysql_sock = options.mysql_sock
        if options.mysql_cnf: mysql_cnf = options.mysql_cnf
        if options.stor_dir: stor_dir = options.stor_dir.rstrip('/')
        if options.work_dir: work_dir = options.work_dir.rstrip('/')
        if options.retention_binlogs: retention_binlogs = options.retention_binlogs
        if options.compress: compress = options.compress
        if options.compress_with: compress_with = options.compress_with
        if options.notify_by_email: notify_by_email = options.notify_by_email
        if options.notify_on_success: notify_on_success = options.notify_on_success
        if options.first_binlog: first_binlog = options.first_binlog
        if options.binlog_binary: binlog_binary = options.binlog_binary
        if options.binlog_from_master: binlog_from_master = options.binlog_from_master

        if options.remote_stor_dir: remote_stor_dir = options.remote_stor_dir
        if options.remote_host: remote_host = options.remote_host
        if options.remote_script: remote_script = options.remote_script
        if options.remote_push_only is not None:
            remote_push_only = options.remote_push_only
        
        if options.remote_nc_port is not None and \
                not _parse_port_param(options.remote_nc_port):
            parser.error("The specified port (range) is not valid")
        else:
            remote_nc_port = options.remote_nc_port

        if options.ssh_opts: ssh_opts = options.ssh_opts
        if options.ssh_user: ssh_user = options.ssh_user
        if options.meta_item: meta_item = options.meta_item

        if remote_host is not None and remote_stor_dir is None:
            parser.error("Remote host specified but, remote store directory is empty")

        if options.apply_log: apply_log = options.apply_log
        if options.prepare_memory: prepare_memory = options.prepare_memory
        if options.status_format: status_format = options.status_format
        if options.restore_backup is not None:
            restore_backup = options.restore_backup
        if options.restore_dir is not None:
            restore_dir = options.restore_dir
        if options.retention_sets and int(options.retention_sets) > 0:
            retention_sets = int(options.retention_sets)
        if options.retention_months > 0:
            retention_months = int(options.retention_months)
        if options.retention_weeks > 0:
            retention_weeks = int(options.retention_weeks)

        if options.encrypt: encrypt = options.encrypt
        if options.encrypt_key_file: encrypt_key_file = options.encrypt_key_file
        if options.extra_ibx_options: extra_ibx_options = options.extra_ibx_options
        if options.purge_bitmaps: purge_bitmaps = options.purge_bitmaps

        if cfg: _debug('Found config file: ', config)

        cmds = [XB_CMD_FULL, XB_CMD_INCR, XB_CMD_LIST, XB_CMD_STAT, 
                XB_CMD_PREP, XB_CMD_APPL, XB_CMD_PRUNE, XB_CMD_META, 
                XB_CMD_BINLOGS, XB_CMD_WIPE]
        if len(args) >= 1 and args[0] not in cmds:
            parser.error("Command not recognized, got '%s'. See more with --help" % args[0])
        elif len(args) <= 0:
            parser.error("Command not specified. See more with --help")
        else:
            command = args[0]

        if remote_push_only and apply_log:
            _die("--remote-push-only and --apply-log are mutually exclusive")

        if options.retention_sets is not None and options.retention_sets <= 0:
            _die("Invalid value for retention sets, ",
                "you should keep one or more backup sets!")

        if encrypt and not os.path.isfile(encrypt_key_file):
            _die("The specified key file does not exist!")

        if encrypt and compress and compress_with == 'gzip':
            _die("GZIP compression + encryption is not supported ",
                "at the moment. Please use --compress-with=qpress instead.")

        if encrypt and not compress:
            _die("Encryption requires compression for now, support for ",
                "uncompressed encrypted backup will be added in the future")

        if command in [XB_CMD_FULL, XB_CMD_INCR, XB_CMD_PREP, XB_CMD_APPL]:
            _check_binary('innobackupex')
            _check_binary('xtrabackup')

        if remote_nc_port_min:
            _check_binary('nc')
            _check_binary('netstat')

        # store xtrabackup version numbers
        _xb_version()

        # we test email delivery beforehand to make sure it works
        # this will happen only once as long as the sentinel file exists
        # i.e. STOR_DIR/pyxbackup_mail_ok
        mail_status_file = "%s/%s_mail_ok" % (stor_dir, XB_BIN_NAME)
        if (notify_by_email or notify_on_success) and \
                not os.path.isfile(mail_status_file):
            mail_message = "This is a test message from %s@%s, please ignore." % (
                xb_user, xb_hostname)
            mail_subject = "pyxbackup Test Mail"
            mail_to = notify_by_email \
                if notify_by_email else notify_on_success

            _say("Mail has not been tested, sending initial test mail.")

            if _notify_by_email(mail_subject, mail_message, mail_to):
                open(mail_status_file, 'a').close()

        if debug:
            _debug("Supplied options:")
            for x, v in options.__dict__.items():
                _debug(("\t%s: %s" % (x, globals()['' + str(x)])))
            _debug("\tcommand: %s" % command)


    def read_config_file(cfg_file, config_section):
        cfg = ConfigParser()
        cfg.read(config)

        if cfg.has_option(config_section, 'mysql_host'):
            mysql_host = cfg.get(config_section, 'mysql_host')

        if cfg.has_option(config_section, 'mysql_user'):
            mysql_user = cfg.get(config_section, 'mysql_user')

        if cfg.has_option(config_section, 'mysql_pass'):
            mysql_pass = cfg.get(config_section, 'mysql_pass')

        if cfg.has_option(config_section, 'mysql_port'):
            mysql_port = int(cfg.get(config_section, 'mysql_port'))

        if cfg.has_option(config_section, 'mysql_sock'):
            mysql_sock = cfg.get(config_section, 'mysql_sock')

        if cfg.has_option(config_section, 'mysql_cnf'):
            mysql_cnf = cfg.get(config_section, 'mysql_cnf')

        if cfg.has_option(config_section, 'stor_dir'):
            stor_dir = cfg.get(config_section, 'stor_dir').rstrip('/')

        if cfg.has_option(config_section, 'work_dir'):
            work_dir = cfg.get(config_section, 'work_dir').rstrip('/')

        if cfg.has_option(config_section, 'ssh_opts'):
            ssh_opts = cfg.get(config_section, 'ssh_opts')

        if cfg.has_option(config_section, 'ssh_user'):
            ssh_user = cfg.get(config_section, 'ssh_user')

        if cfg.has_option(config_section, 'remote_stor_dir'):
            remote_stor_dir = cfg.get(config_section, 'remote_stor_dir').rstrip('/')

        if cfg.has_option(config_section, 'remote_host'):
            remote_host = cfg.get(config_section, 'remote_host')

        if cfg.has_option(config_section, 'remote_script'):
            remote_script = cfg.get(config_section, 'remote_script')

        if cfg.has_option(config_section, 'remote_push_only'):
            remote_push_only = bool(int(cfg.get(config_section, 'remote_push_only')))

        if cfg.has_option(config_section, 'remote_nc_port'):
            if not self.parse_port(cfg.get(config_section, 'remote_nc_port')):
                _die("The specified port (range) is not valid")
            else:
                remote_nc_port = cfg.get(config_section, 'remote_nc_port')

        if cfg.has_option(config_section, 'retention_binlogs'):
            retention_binlogs = int(cfg.get(config_section, 'retention_binlogs'))

        if cfg.has_option(config_section, 'binlog_binary'):
            binlog_binary = cfg.get(config_section, 'binlog_binary')

        if cfg.has_option(config_section, 'binlog_from_master'):
            binlog_from_master = cfg.get(config_section, 'binlog_from_master')

        if cfg.has_option(config_section, 'compress'):
            compress = bool(int(cfg.get(config_section, 'compress')))

        if cfg.has_option(config_section, 'compress_with'):
            compress_with = cfg.get(config_section, 'compress_with')

        if cfg.has_option(config_section, 'notify_by_email'):
            notify_by_email = cfg.get(config_section, 'notify_by_email')

        if cfg.has_option(config_section, 'notify_on_success'):
            notify_on_success = cfg.get(config_section, 'notify_on_success')

        if cfg.has_option(config_section, 'apply_log'):
            apply_log = bool(int(cfg.get(config_section, 'apply_log')))

        if cfg.has_option(config_section, 'prepare_memory'):
            prepare_memory = int(cfg.get(config_section, 'prepare_memory'))

        if cfg.has_option(config_section, 'retention_sets'):
            if int(cfg.get(config_section, 'retention_sets')) > 0:
                retention_sets = int(cfg.get(config_section, 'retention_sets'))

        if cfg.has_option(config_section, 'retention_months'):
            if int(cfg.get(config_section, 'retention_months')) > 0:
                retention_months = int(cfg.get(config_section, 'retention_months'))

        if cfg.has_option(config_section, 'retention_weeks'):
            if int(cfg.get(config_section, 'retention_weeks')) > 0:
                retention_weeks = int(cfg.get(config_section, 'retention_weeks'))

        if cfg.has_option(config_section, 'encrypt_key_file'):
            encrypt_key_file = cfg.get(config_section, 'encrypt_key_file')

        if cfg.has_option(config_section, 'encrypt'):
            encrypt = cfg.get(config_section, 'encrypt')

        if cfg.has_option(config_section, 'extra_ibx_options'):
            extra_ibx_options = cfg.get(config_section, 'extra_ibx_options')

        if cfg.has_option(config_section, 'purge_bitmaps'):
            purge_bitmaps = cfg.get(config_section, 'purge_bitmaps')

        return cfg

    def parse_port(param):
        """
        Parses and assign given port range values 
        i.e.
        remote_nc_port = 9999
        remote_nc_port = 9999,1000
        """

        if not param: return False
        if param.isdigit():
            self.remote_nc_port_min = int(param)
            self.remote_nc_port_max = self.remote_nc_port_min
            return True
        elif param.count(',') == 1:
            pmin, pmax = param.split(',')
            pmin = pmin.strip()
            pmax = pmax.strip()
            if not pmin.isdigit() or not pmax.isdigit(): return False
            self.remote_nc_port_min = int(pmin)
            self.remote_nc_port_max = int(pmax)
            if self.remote_nc_port_min > self.remote_nc_port_max:
                pmin = self.remote_nc_port_max
                self.remote_nc_port_max = self.remote_nc_port_min
                self.remote_nc_port_min = pmin
            return True

        return False

class PyxMail(object):
    pass

class PyxLogger(object):
    pass

class PyxStorage(object):
    pass

class PyxBinlogs(object):
    pass

class PyxBackup(object):
    pass
