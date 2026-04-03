#!/bin/bash
export HARNESS_TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJHaXRuZXNzIiwiaWF0IjoxNzc1MTg0OTUwLCJwaWQiOjQsInRrbiI6eyJ0eXAiOiJwYXQiLCJpZCI6MTF9fQ.cVuK_OM078QmKi9LjeS7XrPsYNSxxSeYL4M3iSma2R8"
export HARNESS_BASE_URL="http://localhost:3000"
export HARNESS_SPACE="test"
cd /e/lizekai/harness/agent
exec /c/Users/A-AAA-202109-83/miniconda3/python.exe daemon.py
