NAME=cloudmask
CODE_DIR=..
BUILD=cloudmask.def
IMAGE=cloudmask
DATA=/scratch2/data/cloudmask/data
DGX_DATA=/raid/gregor/data/cloudmask
BIND=--bind ${DATA}:${DATA}
DGX_BIND=--bind "${DGX_DATA}:/data,/raid/gregor/laszewsk/mlcommons/benchmarks/cloudmask/target/ubuntu-singularity:/cloudmask"

image:
	time ../bin/singularity-image.py ${NAME}

queue: watch

delete:
	rm -f *.out *.err
	rm -f *_output.ipynb
	rm *.log

# TODO
submit:
	sbatch simple-a100-singularity.sh

watch: status

status:
	watch squeue --format=\"%.18i %.9P %.50j %.8u %.8T %.10M %.9l %.6D %R\" --me

run:
	cd ${CODE_DIR}; mkdir -p outputs
	cd ${CODE_DIR}; singularity exec ${BIND} --nv ${IMAGE}.sif bash -c "python cloudmask_v2.py --config=config-new.yaml"

run-dgx:
	cd ${CODE_DIR}; mkdir -p outputs
	cd ${CODE_DIR}; singularity exec ${DGX_BIND} --nv ${IMAGE}.sif bash -c "cd /cloudmask; python cloudmask_v2.py --config=config-dgx.yaml"


shell-dgx:
	singularity shell ${DGX_BIND} --nv ${IMAGE}.sif bash -c "cd /cloudmask/ubuntu-singularity"

#singularity exec --nv ${NAME}.sif papermill ${NAME}.ipynb ${NAME}_output.ipynb

shell:
	singularity ${BIND} shell --nv ${IMAGE}.sif

cancel: stop

stop:
	for i in "$$(squeue --user $$USER | awk 'NR>1{print $$1}')"; do scancel $$i ; done

view:
	watch tail -n 50 *.err

all: delete clean image submit view
