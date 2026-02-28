release-%:
	hatch version $*
	git add parsehealthlog/__init__.py
	git commit -m "chore: release $$(hatch version)"
	git push
