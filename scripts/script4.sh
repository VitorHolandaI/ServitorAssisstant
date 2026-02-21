#!/bin/bash
source /home/vitor/miniconda3/etc/profile.d/conda.sh
conda activate base
cd /home/vitor/git/ServitorAssisstant/api/mcp_module/stremable_http
python3 tasks_server.py
