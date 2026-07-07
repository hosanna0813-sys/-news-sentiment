"""共用的背景工作進度查詢端點，供匯出/抓取/留用初判/議題分群頁的進度條輪詢。"""
from __future__ import annotations

from flask import Blueprint, jsonify

from app.repositories.job_repository import JobRepository
from app.web.job_runner import job_status_dict

jobs_bp = Blueprint("jobs", __name__)


@jobs_bp.route("/jobs/<job_id>/status")
def job_status(job_id):
    status = job_status_dict(JobRepository(), job_id)
    if status is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(status)
