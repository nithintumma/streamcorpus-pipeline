#!/usr/bin/env python
'''
Provides a data transformation pipeline for expanding the data in
StreamItem instances from streamcorpus.Chunk files.  kba.pipeline.run
provides a command line interface to this functionality.

This software is released under an MIT/X11 open source license.

Copyright 2012 Diffeo, Inc.
'''

import os
import re
import sys
import time
import uuid
import math
import signal
import logging
import traceback
import itertools
import streamcorpus

from _logging import log_full_file
from _stages import _init_stage
from _exceptions import FailedExtraction, GracefulShutdown
import _exceptions

logger = logging.getLogger(__name__)

class Pipeline(object):
    '''    
    configurable pipeline for extracting data into StreamItem
    instances, transforming them, and creating streamcorpus.Chunk
    files.  Requires a config dict, which is loaded from a yaml file.
    '''
    def __init__(self, config):
        assert 'kba.pipeline' in config, \
            '"kba.pipeline" missing from config: %r' % config
        config = config['kba.pipeline']
        self.config = config

        self._shutting_down = False

        if not os.path.exists(config['tmp_dir_path']):
            try:
                os.makedirs(config['tmp_dir_path'])
            except Exception, exc:
                logger.debug('tmp_dir_path already there? %r' % exc)
                pass

        if 'rate_log_interval' in self.config:
            self.rate_log_interval = self.config['rate_log_interval']
        else:
            self.rate_log_interval = 100

        if 'external_stages_path' in config:
            import imp
            try:
                external_stages = imp.load_source('', config['external_stages_path'])
            except Exception, exc:
                logger.critical('failed to load external_stages_path: %r' % config['external_stages_path'])
                sys.exit(traceback.format_exc(exc))
            external_stages = external_stages.Stages
        else:
            external_stages = None

        logger.critical('starting a task_queue')

        ## load the one task queue
        task_queue_name = config['task_queue']
        self._task_queue = _init_stage(
            task_queue_name,
            config.get(task_queue_name, {}),
            external_stages)

        ## load the one extractor
        extractor_name = config['extractor']
        self._extractor = _init_stage(
            extractor_name,
            config.get(extractor_name, {}),
            external_stages)

        ## a list of transforms that take StreamItem instances as
        ## input and emit modified StreamItem instances
        self._incremental_transforms = [
            _init_stage(name, config.get(name, {}),
                        external_stages)
            for name in config['incremental_transforms']]

        ## a list of transforms that take a chunk path as input and
        ## return a path to a new chunk
        self._batch_transforms = [
            _init_stage(name, config.get(name, {}),
                        external_stages)
            for name in config['batch_transforms']]

        ## a list of transforms that take a chunk path as input and
        ## return a path to a new chunk
        if 'post_batch_incremental_transforms' in config:
            self._pbi_stages = [
                _init_stage(name, config.get(name, {}),
                            external_stages)
                for name in config['post_batch_incremental_transforms']]
        else:
            self._pbi_stages = []

        ## a list of transforms that take a chunk path as input and
        ## return a path to a new chunk
        self._loaders  = [
            _init_stage(name, config.get(name, {}),
                        external_stages)
            for name in config['loaders']]

        for sig in [signal.SIGTERM, signal.SIGABRT, signal.SIGHUP, signal.SIGINT]:
            logger.debug('setting signal handler for %r' % sig)
            signal.signal(sig, self.shutdown)

    def shutdown(self, sig=None, frame=None, msg=None, exit_code=None):
        if sig:
            logger.critical('shutdown inititated by signal: %r' % sig)
        elif msg:
            logger.critical('shutdown inititated, msg: %s' % msg)
        self._shutting_down = True
        self._task_queue.shutdown()
        for transform in self._batch_transforms:
            transform.shutdown()
        if exit_code is None:
            if msg:
                exit_code = -1
            elif sig:
                exit_code = 128 + sig
        logger.critical('shutdown in final steps, exit_code=%d' % exit_code)
        logging.shutdown()
        sys.exit(exit_code)


    def run(self):
        '''
        operate pipeline on chunks loaded from task_queue
        '''
        start_processing_time = time.time()

        self.context = dict(
            i_str = None,
            data = None, 
            )

        ## iterate over input strings from the specified task_queue
        tasks = iter(self._task_queue)
        while 1:
            try:
                start_count, i_str, data = tasks.next()

                self.context['i_str'] = i_str
                self.context['data'] = data

            except StopIteration:
                break
            except GracefulShutdown, exc:
                ## do a graceful shutdown instead of just a simple
                ## clean exit, because we want to do the various
                ## .shutdown() calls, e.g. to the task_queue itself
                exit_code = 0
                self.shutdown(
                    exit_code=exit_code,
                    msg='executing %r, exit_code=%d' % (exc, exit_code))

            if self._shutting_down:
                break

            start_chunk_time = time.time()

            ## the extractor returns generators of StreamItems
            try:
                i_chunk = self._extractor(i_str)
            except FailedExtraction, exc:
                ## means that task is invalid, extractor did its best
                ## and gave up, so record it in task_queue as failed
                logger.critical('committing failure_log on %s: %r' % (
                        i_str, str(exc)))
                self._task_queue.commit(0, [], failure_log=str(exc))
                continue

            ## t_path points to the currently in-progress temp chunk
            t_path = None

            ## loop over all docs in the chunk processing and cutting
            ## smaller chunks if needed
            i_chunk_iter = iter(i_chunk)

            len_clean_visible = 0
            sources = set()
            next_idx = 0
            hit_last = False
            while not hit_last:

                if self._shutting_down:
                    break
                else:
                    ## must yield to the gevent hub to allow other
                    ## things to run, in particular test_pipeline
                    ## needs this to verify that shutdown works.  We
                    ## import gevent here instead of above out of
                    ## paranoia that the kazoo module used in
                    ## ZookeeperTaskQueue may need to do a monkey
                    ## patch before this gets imported:
                    try:
                        import gevent
                    except:
                        ## only load gevent if it is available :-)
                        gevent = None
                    if gevent:
                        logger.debug('pipeline yielded to gevent hub')
                        gevent.sleep(0)

                try:
                    si = i_chunk_iter.next()
                    next_idx += 1
                except StopIteration:
                    hit_last = True
                    if next_idx == start_count:
                        ## means that output_chunk_max_count is equal
                        ## to length of chunk, so we have already done
                        ## a partial commit, and can just finish with
                        ## a final commit
                        self._task_queue.commit( next_idx, [] )
                        break

                ## skip forward until we reach start_count
                if next_idx <= start_count:
                    assert not hit_last, 'hit_last next_idx = %d <= %d = start_count' \
                        % (next_idx, start_count)
                    continue

                if next_idx % self.rate_log_interval == 0:
                    ## indexing is zero-based, so next_idx corresponds
                    ## to length of list of SIs processed so far
                    elapsed = time.time() - start_chunk_time
                    if elapsed > 0:
                        rate = float(next_idx) / elapsed
                        logger.info('%d in %.1f --> %.1f per sec on (pre-partial_commit) %s' % (
                            next_idx - start_count, elapsed, rate, i_str))

                if not t_path:
                    ## make a temporary chunk at a temporary path
                    t_path = os.path.join(self.config['tmp_dir_path'], 'trec-kba-pipeline-tmp-%s' % str(uuid.uuid4()))
                    self.t_chunk = streamcorpus.Chunk(path=t_path, mode='wb')

                ## incremental transforms populate t_chunk
                if not hit_last: # avoid re-adding last si
                    ## let the incremental transforms destroy the si by returning None
                    si = self._run_incremental_transforms(si, self._incremental_transforms)

                ## insist that every chunk has only one source string
                if si:
                    sources.add( si.source )
                    assert len(sources) == 1, sources

                if si and si.body and si.body.clean_visible:
                    len_clean_visible += len(si.body.clean_visible)
                    ## log binned clean_visible lengths, for quick stats estimates
                    #logger.debug('len(si.body.clean_visible)=%d' % int(10 * int(math.floor(float(len(si.body.clean_visible)) / 2**10)/10)))
                    logger.debug('len(si.body.clean_visible)=%d' % len(si.body.clean_visible))

                if 'output_chunk_max_count' in self.config and \
                        len(self.t_chunk) == self.config['output_chunk_max_count']:
                    logger.warn('reached output_chunk_max_count (%d) at: %d' % (len(self.t_chunk), next_idx))
                    self.t_chunk.close()
                    ## will execute steps below

                elif 'output_chunk_max_clean_visible_bytes' in self.config and \
                        len_clean_visible >= self.config['output_chunk_max_clean_visible_bytes']:
                    logger.warn('reached output_chunk_max_clean_visible_bytes (%d) at: %d' % (
                            self.config['output_chunk_max_clean_visible_bytes'], len_clean_visible))
                    len_clean_visible = 0
                    self.t_chunk.close()
                    ## will execute steps below

                elif not hit_last:
                    ## keep looping until we hit the end of the i_chunk
                    continue

                else:
                    assert hit_last
                    self.t_chunk.close()

                ## gather the paths as the loaders run
                o_paths = []
                if len(self.t_chunk) > 0:
                    ## only batch transform and load if the chunk
                    ## isn't empty, which can happen when filtering
                    ## with stages like "find"
                    
                    ## batch transforms act on the whole chunk in-place
                    self._run_batch_transforms(t_path)

                    ## Run post batch incremental (pbi) transform stages.
                    ## These exist because certain batch transforms have 
                    ## to run before certain incremental stages.
                    t_path2 = os.path.join(self.config['tmp_dir_path'], 'trec-kba-pipeline-tmp-%s' % str(uuid.uuid1()))
                    self.t_chunk = streamcorpus.Chunk(path=t_path2, mode='wb')

                    input_t_chunk = streamcorpus.Chunk(path=t_path, mode='rb')
                    for si in input_t_chunk:
                        si = self._run_incremental_transforms(si, self._pbi_stages)

                    self.t_chunk.close()

                    if len(self.t_chunk) == 0:
                        continue

                    ## loaders put the chunk somewhere, and could delete it
                    name_info = dict(
                        first = start_count,
                        #num and md5 computed in each loaders
                        source = sources.pop(),
                        )
                   
                    for loader in self._loaders:
                        try:
                            logger.debug('running %r on %r: %r' % (loader, i_str, name_info))
                            o_path = loader(t_path, name_info, i_str)                        
                        except OSError, exc:
                            if exc.errno == 12:
                                logger.critical('caught OSError 12 in loader, so shutting down')
                                self.shutdown( msg=traceback.format_exc(exc) )
                            else:
                                logger.critical(traceback.format_exc(exc))
                                raise exc

                        logger.debug('loaded (%d, %d) of %r into %r' % (
                                start_count, next_idx - 1, i_str, o_path))
                        if o_path:
                            o_paths.append( o_path )

                        if self._shutting_down:
                            break

                if self._shutting_down:
                    break

                if not hit_last:
                    ## commit the paths saved so far
                    logger.debug('partial_commit( %d, %d, %r )' % (start_count, next_idx, o_paths))
                    self._task_queue.partial_commit( start_count, next_idx, o_paths )

                    ## reset t_path, so we get it again
                    t_path = None

                    elapsed = time.time() - start_chunk_time
                    if elapsed > 0:
                        rate = float(next_idx) / elapsed
                        logger.info('%d more of %d in %.1f --> %.1f per sec on (post-partial_commit) %s' % (
                            next_idx - start_count, next_idx, elapsed, rate, i_str))

                    ## advance start_count for next loop
                    logger.info('advancing start_count from %d to %d' % (start_count, next_idx))
                    start_count = next_idx

                else:
                    ## put the o_paths into the task_queue, and set
                    ## the task to 'completed'
                    logger.debug('commit( %d, %r )' % (next_idx, o_paths))
                    self._task_queue.commit( next_idx, o_paths )

            ## record elapsed time
            elapsed = time.time() - start_chunk_time
            if elapsed > 0:
                rate = float(next_idx) / elapsed
                logger.info('%d in %.1f sec --> %.1f per sec on (completed) %s' % (
                        next_idx, elapsed, rate, i_str))

            ## loop to next i_str
        logger.critical('exiting Pipeline.run')

    def _run_batch_transforms(self, chunk_path):
        for transform in self._batch_transforms:
            try:
                transform(chunk_path)
            except _exceptions.PipelineOutOfMemory, exc:
                logger.critical('caught PipelineOutOfMemory, so shutting down')
                self.shutdown( msg=traceback.format_exc(exc) )
            except Exception, exc:
                if self._shutting_down:
                    logger.critical('ignoring exception while shutting down: %s' % \
                                        traceback.format_exc(exc))
                else:
                    ## otherwise, let it bubble up and kill this process
                    logger.critical(traceback.format_exc(exc))
                    raise exc

    def _run_incremental_transforms(self, si, transforms):
        ## operate each transform on this one StreamItem
        for transform in transforms:
            #timer = gevent.Timeout.start_new(1)
            #thread = gevent.spawn(transform, si)
            #try:
            #    si = thread.get(timeout=timer)
            ### The approach above to timeouts did not work,
            ### because when the re module hangs in a thread, it
            ### never yields to the greenlet hub.  The only robust
            ### way to implement timeouts is with child processes,
            ### possibly via multiprocessing.  Another benefit of
            ### child processes is armoring against segfaulting in
            ### various libraries.  This probably means that each
            ### transform should implement its own timeouts.
            try:
                si = transform(si, context=self.context)
                if si is None:
                    break

            except _exceptions.TransformGivingUp:
                ## do nothing
                logger.info('transform %r giving up on %r' % (transform, si.stream_id))
                pass

            except Exception, exc:
                logger.critical('Pipeline trapped: %s' % traceback.format_exc(exc))

                if self.config.get('embedded_logs', None):
                    si.body.logs.append( traceback.format_exc(exc) )

                if self.config.get('log_dir_path', None):
                    log_full_file(si, 'fallback-givingup', self.config['log_dir_path'])

        if si is not None:
            ## expect to always have a stream_time
            if not si.stream_time:
                msg = 'empty stream_time: %s' % si
                logger.critical(msg)
                sys.exit(msg)

            if si.stream_id is None:
                logger.critical('empty stream_id: %r' % si)
                si = None

            else:
                ## put the StreamItem into the output
                self.t_chunk.add(si)

        return si