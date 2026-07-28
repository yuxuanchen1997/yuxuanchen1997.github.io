.PHONY: install build daily clean serve

install:
	npm install

build:
	npm run build
	cp -R public/. ./

daily:
	python3 source/daily_story.py
	$(MAKE) build

serve:
	npx hexo server

clean:
	npx hexo clean
