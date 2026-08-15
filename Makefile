# Every target delegates to sz.py so `make` and `sz` can never drift apart.
# Windows has no make; use `python sz.py <target>` there (or sz.cmd).

PY ?= python

.PHONY: help install check test lint typecheck fetch-models \
        smoke run-benign run-attack collect collect-sessions \
        train-hazard estimate-pomdp solve-pomdp build-novelty train \
        run-arena adapt eval eval-quick ui replay demo record-demo \
        verify-log attacker victim solo

help:
	@$(PY) sz.py help

install:        ; @$(PY) sz.py install
check:          ; @$(PY) sz.py check
test:           ; @$(PY) sz.py test
lint:           ; @$(PY) sz.py lint
typecheck:      ; @$(PY) sz.py typecheck
fetch-models:   ; @$(PY) sz.py fetch-models

smoke:          ; @$(PY) sz.py smoke
run-benign:     ; @$(PY) sz.py run-benign
run-attack:     ; @$(PY) sz.py run-attack

collect:            ; @$(PY) sz.py collect
collect-sessions:   ; @$(PY) sz.py collect-sessions -n $(or $(N),300)
train-hazard:       ; @$(PY) sz.py train-hazard
estimate-pomdp:     ; @$(PY) sz.py estimate-pomdp
solve-pomdp:        ; @$(PY) sz.py solve-pomdp
build-novelty:      ; @$(PY) sz.py build-novelty
train:              ; @$(PY) sz.py train

run-arena:      ; @$(PY) sz.py run-arena
adapt:          ; @$(PY) sz.py adapt
eval:           ; @$(PY) sz.py eval
eval-quick:     ; @$(PY) sz.py eval-quick
verify-log:     ; @$(PY) sz.py verify-log

ui:             ; @$(PY) sz.py ui
replay:         ; @$(PY) sz.py replay
demo:           ; @$(PY) sz.py demo
record-demo:    ; @$(PY) sz.py record-demo

attacker:       ; @$(PY) sz.py attacker
victim:         ; @$(PY) sz.py victim
solo:           ; @$(PY) sz.py solo
