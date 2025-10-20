#!/bin/bash
python create_tables.py
gunicorn app:app