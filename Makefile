export PATH := /opt/diffeo/bin:$(PATH)
VERSION:=$(shell grep ^VERSION setup.py | cut -d\' -f2)
RELEASE:=2
TMPISODIR:=$(shell mktemp --tmpdir=/var/tmp -d)

clean_js:
	## to make real tests, we need to dump a non-time-sensitive
	## extract from these Chunk files and compare it to a stored
	## copy of what is expected.
	git checkout -- data/john-smith/john-smith-0.sc
	git checkout -- data/john-smith/john-smith-tagged-by-lingpipe-0.sc
	rm -f data/john-smith/john-smith-tagged-by-lingpipe-test-0.sc
	rm -rf tmp

test: clean
	## clean does not always get called!?  Must remove "build" so
	## it does not confuse py.test
	rm -rf build
	py.test --genscript=runtests.py
	cd src && python ../runtests.py -s

john-smith: clean install test
	echo data/john-smith/original | python -m kba.pipeline.run configs/john-smith.yaml
	echo data/john-smith/original | python -m kba.pipeline.run configs/john-smith-lingpipe.yaml
	echo data/john-smith/john-smith-0.sc | python -m kba.pipeline.run configs/john-smith-lingpipe-from-chunk.yaml

	## compare dumps
	python -m streamcorpus.dump data/john-smith/john-smith-tagged-by-lingpipe-0.sc      --tokens > tmp/lp-0.tsv
	python -m streamcorpus.dump data/john-smith/john-smith-tagged-by-lingpipe-test-0.sc --tokens > tmp/lp-test-0.tsv

	diff tmp/lp-0.tsv tmp/lp-test-0.tsv
	diff tmp/lp-0.tsv data/john-smith/john-smith-tagged-by-lingpipe-0.tsv

john-smith-simple: clean install test
	echo data/john-smith/original | python -m kba.pipeline.run configs/john-smith.yaml

stanford: install
	## this is so slow that there must be something wrong with our
	## build of Stanford CoreNLP
	echo data/john-smith/john-smith-0.sc | python -m kba.pipeline.run configs/john-smith-stanford-from-chunk.yaml

clean: clean_js
	rm -rf build dist src/kba.pipeline.egg-info runtests.py *.iso

.IGNORE: lxml
lxml:
	## this should be done by cloudinit/puppet or something along
	## those lines
	#sudo apt-get -y install libxml2-dev libxslt-dev  

post-build-test:
	echo data/john-smith/john-smith-0.sc | python -m kba.pipeline.run configs/john-smith-lingpipe-from-chunk.yaml

install: clean lxml
	## might need to do this on mac: export C_INCLUDE_PATH=/usr/include:/usr/local/include:/opt/local/include
	python setup.py clean --all
	python setup.py build
	python setup.py install

egg: 
	python setup.py bdist_egg
	echo "Newly build egg can be found in the dist/ directory"

centos_rpm:
	python setup.py bdist_rpm --requires=diffeo-common

trec-kba-pipeline-$(VERSION)-$(RELEASE).iso: centos_rpm
	cp diffeo-common/latest_rpm/* $(TMPISODIR)
	cp dist/kba.pipeline-*noarch.*rpm $(TMPISODIR)
	genisoimage -R -J -o trec-kba-pipeline-$(VERSION)-$(RELEASE).iso $(TMPISODIR)
	rm -fR $(TMPISODIR)

centos_iso: trec-kba-pipeline-$(VERSION)-$(RELEASE).iso

