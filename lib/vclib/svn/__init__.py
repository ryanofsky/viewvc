# -*-python-*-
#
# Copyright (C) 1999-2002 The ViewCVS Group. All Rights Reserved.
#
# By using this file, you agree to the terms and conditions set forth in
# the LICENSE.html file which can be found at the top level of the ViewCVS
# distribution or at http://viewcvs.sourceforge.net/license-1.html.
#
# Contact information:
#   Greg Stein, PO Box 760, Palo Alto, CA, 94302
#   gstein@lyra.org, http://viewcvs.sourceforge.net/
#
# -----------------------------------------------------------------------

"Version Control lib driver for locally accessible Subversion repositories."


# ======================================================================

import vclib
import os
import os.path
import string

# Subversion swig libs
from svn import fs, repos, core

# Subversion filesystem paths are '/'-delimited, regardless of OS.
def fs_path_join(base, relative):
  joined_path = base + '/' + relative
  parts = filter(None, string.split(joined_path, '/'))
  return string.join(parts, '/')

  
def _datestr_to_date(datestr, pool):
  return core.svn_time_from_cstring(datestr, pool) / 1000000

  
def _fs_rev_props(fsptr, rev, pool):
  author = fs.revision_prop(fsptr, rev, core.SVN_PROP_REVISION_AUTHOR, pool)
  msg = fs.revision_prop(fsptr, rev, core.SVN_PROP_REVISION_LOG, pool)
  date = fs.revision_prop(fsptr, rev, core.SVN_PROP_REVISION_DATE, pool)
  return date, author, msg


def date_from_rev(svnrepos, rev):
  if (rev < 0) or (rev > fs.youngest_rev(svnrepos.fs_ptr, svnrepos.pool)):
    raise vclib.InvalidRevision(rev);
  datestr = fs.revision_prop(svnrepos.fs_ptr, rev,
                             core.SVN_PROP_REVISION_DATE, svnrepos.pool)
  return _datestr_to_date(datestr, svnrepos.pool)
  

class LogEntry:
  "Hold state for each revision's log entry."
  def __init__(self, rev, date, author, msg, filename, other_paths,
               action, copy_path, copy_rev):
    self.rev = rev
    self.date = date
    self.author = author
    self.state = '' # should we populate this?
    self.changed = 0
    self.log = msg
    self.filename = filename
    self.other_paths = other_paths
    self.action = action
    self.copy_path = copy_path
    self.copy_rev = copy_rev

class ChangedPathEntry:
  def __init__(self, filename):
    self.filename = filename

class LogReceiver:
  "Handler for Subversion log message chunks"
  def __init__(self, filename, fs_ptr):
    self.logs = { }
    self.filename = filename
    self.fs_ptr = fs_ptr

  def receive(self, paths, revision, author, datestr, msg, pool):
    root = fs.revision_root(self.fs_ptr, revision, pool)

    action = copy_path = copy_rev = None
    filename = self.filename
    other_paths = [ ]

    if paths:

      import sapi
      sapi.server.header()

      subpool = core.svn_pool_create(pool)
      for path, change in paths.items():
        core.svn_pool_clear(subpool)

        kind = fs.check_path(root, path, subpool)
        if kind != core.svn_node_file:
          continue

        assert path[0] == '/'
        path = path[1:]

        if path == filename:
          assert action is None and copy_path is None and copy_rev is None
          action = change.action
          if change.copyfrom_path:
            assert change.copyfrom_path[0] == '/'
            copy_path = self.filename = change.copyfrom_path[1:]
            copy_rev = change.copyfrom_rev
        else:
          other_paths.append(ChangedPathEntry(path))

      core.svn_pool_destroy(subpool)
      
      assert action is not None

    date = _datestr_to_date(datestr, pool)

    log = LogEntry(revision, date, author, msg, filename,
                   other_paths, action, copy_path, copy_rev)

    log.size = fs.file_length(root, filename, pool)

    self.logs[revision] = log
    
def get_logs(svnrepos, full_name, files):
  fileinfo = { }
  alltags = {           # all the tags seen in the files of this dir
    'MAIN' : '1',
    'HEAD' : '1',
    }
  for file in files:
    path = fs_path_join(full_name, file)
    rev = fs.node_created_rev(svnrepos.fsroot, path, svnrepos.pool)
    datestr, author, msg = _fs_rev_props(svnrepos.fs_ptr, rev, svnrepos.pool)
    date = _datestr_to_date(datestr, svnrepos.pool)
    new_entry = LogEntry(rev, date, author, msg, file, [], None, None, None)
    if fs.is_file(svnrepos.fsroot, path, svnrepos.pool):
      new_entry.size = fs.file_length(svnrepos.fsroot, path, svnrepos.pool)
    fileinfo[file] = new_entry
  return fileinfo, alltags


def fetch_log(svnrepos, full_name, which_rev=None):
  receiver = LogReceiver(full_name, svnrepos.fs_ptr);
  alltags = {           # all the tags seen in the files of this dir
    'MAIN' : '1',
    'HEAD' : '1',
    }
  if which_rev is not None:
    if (which_rev < 0) \
       or (which_rev > fs.youngest_rev(svnrepos.fs_ptr, svnrepos.pool)):
      raise vclib.InvalidRevision(which_rev);
    
    datestr, author, msg = _fs_rev_props(svnrepos.fs_ptr,
                                         which_rev, svnrepos.pool)
    receiver.receive(which_rev, author, datestr, msg, svnrepos.pool)
  else:
    repos.svn_repos_get_logs(svnrepos.repos, [ full_name ],
                             svnrepos.rev, 0,
                             getattr(svnrepos, 'get_changed_paths', 1), 1,
                             receiver.receive, svnrepos.pool)
  return alltags, receiver.logs


def do_diff(svnrepos, path, rev1, rev2, diffoptions):
  root1 = fs.revision_root(svnrepos.fs_ptr, rev1, svnrepos.pool)
  root2 = fs.revision_root(svnrepos.fs_ptr, rev2, svnrepos.pool)
  return fs.FileDiff(root1, path, root2, path, svnrepos.pool, diffoptions)


class StreamPipe:
  def __init__(self, stream):
    self._stream = stream
    self._eof = 0
    
  def read(self, len):
    chunk = None
    if not self._eof:
      chunk = core.svn_stream_read(self._stream, len)
    if not chunk:
      self._eof = 1
    return chunk
  
  def readline(self):
    chunk = None
    if not self._eof:
      chunk = core.svn_stream_readline(self._stream)
    if not chunk:
      self._eof = 1
    return chunk

  def close(self):
    return core.svn_stream_close(self._stream)

  def eof(self):
    return self._eof
    
def get_file_contents(svnrepos, path):
  # len = fs.file_length(svnrepos.fsroot, path, svnrepos.pool)
  stream = fs.file_contents(svnrepos.fsroot, path, svnrepos.pool)
  return StreamPipe(stream)

  
class SubversionRepository(vclib.Repository):
  def __init__(self, name, rootpath, rev=None):
    if not os.path.isdir(rootpath):
      raise vclib.ReposNotFound(name)
    core.apr_initialize()
    self.pool = core.svn_pool_create(None)
    self.repos = repos.svn_repos_open(rootpath, self.pool)
    self.name = name
    self.rootpath = rootpath
    self.fs_ptr = repos.svn_repos_fs(self.repos)
    self.rev = rev
    youngest = fs.youngest_rev(self.fs_ptr, self.pool);
    if self.rev is None:
      self.rev = youngest
    if (self.rev < 0) or (self.rev > youngest):
      raise vclib.InvalidRevision(self.rev);
    self.fsroot = fs.revision_root(self.fs_ptr, self.rev, self.pool)

  def __del__(self):
    core.svn_pool_destroy(self.pool)
    core.apr_terminate()
    
  def getitem(self, path_parts):
    basepath = self._getpath(path_parts)
    item = self.itemtype(path_parts)
    if item is vclib.DIR:
      return vclib.Versdir(self, basepath)
    else:
      return vclib.Versfile(self, basepath)

  def itemtype(self, path_parts):
    basepath = self._getpath(path_parts)
    kind = fs.check_path(self.fsroot, basepath, self.pool)
    if kind == core.svn_node_dir:
      return vclib.DIR
    if kind == core.svn_node_file:
      return vclib.FILE
    raise vclib.ItemNotFound(path_parts)

  def _getpath(self, path_parts):
    return string.join(path_parts, '/')

  def _getvf_subdirs(self, basepath):
    entries = fs.dir_entries(self.fsroot, basepath, self.pool)
    subdirs = { }
    names = entries.keys()
    names.sort()
    subpool = core.svn_pool_create(self.pool)
    for name in names:
      child = fs_path_join(basepath, name)
      if fs.is_dir(self.fsroot, child, subpool):
        subdirs[name] = vclib.Versdir(self, child)
      core.svn_pool_clear(subpool)
    core.svn_pool_destroy(subpool)
    return subdirs
    
  def _getvf_files(self, basepath):
    entries = fs.dir_entries(self.fsroot, basepath, self.pool)
    files = { }
    names = entries.keys()
    names.sort()
    subpool = core.svn_pool_create(self.pool)
    for name in names:
      child = fs_path_join(basepath, name)
      if fs.is_file(self.fsroot, child, subpool):
        files[name] = vclib.Versfile(self, child)
      core.svn_pool_clear(subpool)
    core.svn_pool_destroy(subpool)
    return files
  
  def _getvf_info(self, target, basepath):
    if not fs.is_file(self.fsroot, basepath, self.pool):
      raise "Unknown file: %s " % basepath
    # todo
    pass
  
  def _getvf_tree(self, versfile):
    """
    should return a dictionary of Revisions
    Developers: method to be overloaded.
    """
    # todo

  def _getvf_properties(self, target, basepath, revisionnumber):
    """
    Add/update into target's attributes (expected to be an instance of
    Revision) a certain number of attributes:
    rev
    date
    author
    state
    log
    previous revision number
    branches ( a list of revision numbers )
    changes ( string of the form: e.g. "+1 -0 lines" )
    tags
    ... ( there can be other stuff here)
    
    Developers: in the cvs implementation, the method will never be called.
    There is no point in developping this method as  _getvf_tree already
    gets the properties.
    """
    # todo

  def _getvf_cofile(self, target, basepath):
    """
    should return a file object representing the checked out revision.
    Notice that _getvf_co can also add the properties in <target> the
    way _getvf_properties does.  

    Developers: method to be overloaded.
    """
    # todo

