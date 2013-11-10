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
	py.test --genscript=runtests.py
	python setup.py install_test > /dev/null
	## TODO: make `python setup.py test` do the same as the next
	## line, i.e. ensure that collectonly passes before running,
	## and runs in parallel
	cd src && py.test --collectonly && py.test -n 8

john-smith-lingpipe: john-smith-simple
	echo data/john-smith/original | python -m streamcorpus.pipeline.run configs/john-smith-lingpipe.yaml
	echo data/john-smith/john-smith-0.sc | python -m streamcorpus.pipeline.run configs/john-smith-lingpipe-from-chunk.yaml

	## compare dumps
	python -m streamcorpus.dump data/john-smith/john-smith-tagged-by-lingpipe-0.sc      --tokens > tmp/lp-0.tsv
	python -m streamcorpus.dump data/john-smith/john-smith-tagged-by-lingpipe-test-0.sc --tokens > tmp/lp-test-0.tsv

	diff tmp/lp-0.tsv tmp/lp-test-0.tsv
	diff tmp/lp-0.tsv data/john-smith/john-smith-tagged-by-lingpipe-0.tsv

john-smith-serif: john-smith-simple
	echo data/john-smith/original | python -m streamcorpus.pipeline.run configs/john-smith-serif-streamcorpus_one_step.yaml
	echo data/john-smith/john-smith-0.sc | python -m streamcorpus.pipeline.run configs/john-smith-serif-streamcorpus_generate_serifxml.yaml
	echo data/john-smith/john-smith-tagged-by-serifxml-only.sc | python -m streamcorpus.pipeline.run configs/john-smith-serif-streamcorpus_read_serifxml.yaml

	## compare dumps
	python -m streamcorpus.dump data/john-smith/john-smith-tagged-by-serif-0.sc      --tokens > tmp/serif-0.tsv
	python -m streamcorpus.dump data/john-smith/john-smith-tagged-by-serif-via-serifxml-0.sc    --tokens > tmp/serif-test-0.tsv

	diff tmp/serif-0.tsv data/john-smith/john-smith-tagged-by-serif-0.tsv	
	diff tmp/serif-0.tsv tmp/serif-test-0.tsv

john-smith-stanford: john-smith-simple
	## this is so slow that there must be something wrong with our
	## build of Stanford CoreNLP
	echo data/john-smith/john-smith-0.sc | python -m streamcorpus.pipeline.run configs/john-smith-stanford-from-chunk.yaml

john-smith-all: clean install john-smith-simple john-smith-lingpipe john-smith-serif


clean: clean_js
	rm -rf build dist src/streamcorpus.pipeline.egg-info runtests.py *.iso

.IGNORE: lxml
lxml:
	## this should be done by cloudinit/puppet or something along
	## those lines
	#sudo apt-get -y install libxml2-dev libxslt-dev  

.PHONY : build
build: clean
	python setup.py bdist_egg sdist

post-build-test:
	echo data/john-smith/john-smith-0.sc | python -m streamcorpus.pipeline.run configs/john-smith-lingpipe-from-chunk.yaml

install: clean lxml
	## might need to do this on mac: export C_INCLUDE_PATH=/usr/include:/usr/local/include:/opt/local/include
	python setup.py install_test
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
	cp dist/streamcorpus.pipeline-*noarch.*rpm $(TMPISODIR)
	genisoimage -R -J -o trec-kba-pipeline-$(VERSION)-$(RELEASE).iso $(TMPISODIR)
	rm -fR $(TMPISODIR)

centos_iso: trec-kba-pipeline-$(VERSION)-$(RELEASE).iso

register:
        ## upload both source and binary
	python setup.py sdist bdist_egg upload -r internal

check:
	pylint -i y --output-format=parseable src/`git remote -v | grep origin | head -1 | cut -d':' -f 2 | cut -d'.' -f 1`

