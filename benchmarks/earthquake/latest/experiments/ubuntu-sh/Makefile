
all: data project generate notebooks environment

data:
	python create_data.py

notebooks:
	python create_notebook.py

environment:
	python create_env.py

submit: all
	sh jobs-project.sh

.PHONY: project generate inspect run stop clean status watch out err status

project:
	cms sbatch generate \
	           --source=ubuntu.in.slurm \
	           --noos \
	           --config=ubuntu-config.yaml \
	           --name=project

generate:
	cms sbatch generate submit --type=bash --name=project.json > jobs-project.sh
	cat jobs-project.sh

inspect:
    # works only if we do one experiement
	emacs project/*/slurm.sh

run:
	sh ./jobs-project.sh

stop:
	for i in "$$(squeue --user $$USER | awk 'NR>1{print $$1}')"; do scancel $$i ; done

watch:
	tail -f project/*/$(shell whoami)-*.err project/*/$(shell whoami)-*.out

out: 
	tail -f project/*/$(shell whoami)-*.out

err: 
	tail -f project/*/$(shell whoami)-*.err

status:
	squeue | fgrep ${USER}

clean:
	@-rm -rf project project.json jobs-project.sh
	@-rm -f ubuntu.slurm
	@-rm -rf mlcommons-data-earthquake
