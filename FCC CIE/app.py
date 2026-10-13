"""
Hardened Flask API for decoupled frontend.
Security measures:
- Signed bearer tokens with itsdangerous (1-hour expiry)
- Token validation via @require_admin decorator
- flask-cors with origin/method/header restrictions
- flask-limiter for rate limiting
- Health check with DB connectivity
- 10MB max content length
- Structured JSON logging to stdout
- Production-ready (no debug mode, gunicorn entry point)
"""

import hmac
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

import boto3
from werkzeug.utils import secure_filename
from flask import Flask, request, jsonify, send_file, url_for
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy import Boolean, DateTime, String, Text, create_engine, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, scoped_session, sessionmaker
from sqlalchemy.types import CHAR, TypeDecorator

from config import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    ALLOWED_ORIGIN,
    AWS_REGION,
    DATABASE_URL,
    PORT,
    S3_BUCKET,
    STORAGE_BACKEND,
    SECRET_KEY,
    config,
)

# =============================================================================
# Configuration
# =============================================================================

# Token security: 1-hour expiry
TOKEN_SERIALIZER = URLSafeTimedSerializer(
    secret_key=SECRET_KEY,
    salt='api-auth-salt',
    signer_kwargs={'key_derivation': 'hmac', 'digest_method': 'sha256'}
)
MAX_TOKEN_AGE = 3600  # 1 hour in seconds

# =============================================================================
# Flask App Setup
# =============================================================================

# The frontend is deployed as a separate static site. Flask exposes API routes only.
app = Flask(__name__, static_folder=None)

# Disable debug mode for production
app.config['DEBUG'] = False

# Set max content length to 10 MB
# Allow multipart framing around a file that may itself be up to 10 MB.
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024 + 64 * 1024

# =============================================================================
# Structured JSON Logging
# =============================================================================

class JSONFormatter(logging.Formatter):
    """Format log records as JSON for structured logging."""
    
    def format(self, record):
        log_data = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }
        
        # Add exception info if present
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)
        
        # Add extra fields
        extra_fields = {}
        for key, value in record.__dict__.items():
            if key not in ('name', 'levelname', 'levelno', 'pathname', 'filename',
                          'module', 'lineno', 'funcName', 'created', 'msecs',
                          'relativeCreated', 'thread', 'threadName', 'processName',
                          'process', 'getMessage', 'exc_info', 'exc_text', 'msg'):
                try:
                    json.dumps(value)  # Check if serializable
                    extra_fields[key] = value
                except (TypeError, ValueError):
                    extra_fields[key] = str(value)
        
        if extra_fields:
            log_data['extra'] = extra_fields
        
        return json.dumps(log_data)


# Configure root logger
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# Remove existing handlers
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)

# Add stdout handler with JSON formatting
stdout_handler = logging.StreamHandler()
stdout_handler.setFormatter(JSONFormatter())
root_logger.addHandler(stdout_handler)

# Also configure Flask's app logger
app.logger.setLevel(logging.INFO)
for handler in app.logger.handlers[:]:
    app.logger.removeHandler(handler)
app_logger_handler = logging.StreamHandler()
app_logger_handler.setFormatter(JSONFormatter())
app.logger.addHandler(app_logger_handler)

logger = logging.getLogger(__name__)

# =============================================================================
# Database
# =============================================================================

class Base(DeclarativeBase):
    pass


class UUIDType(TypeDecorator):
    """Store UUIDs natively in PostgreSQL and as strings in SQLite."""

    impl = CHAR(36)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == 'postgresql':
            from sqlalchemy.dialects.postgresql import UUID
            return dialect.type_descriptor(UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return value if dialect.name == 'postgresql' else str(value)

    def process_result_value(self, value, dialect):
        return None if value is None else uuid.UUID(str(value))


class UTCDateTime(TypeDecorator):
    """Normalize datetimes to timezone-aware UTC values across both databases."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Submission(Base):
    __tablename__ = 'submissions'

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    has_upload: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    upload_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


engine_kwargs = {'pool_pre_ping': True}
if DATABASE_URL.startswith('sqlite'):
    engine_kwargs['connect_args'] = {'check_same_thread': False}
engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionFactory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
db_session = scoped_session(SessionFactory)


def create_tables():
    Base.metadata.create_all(engine)


create_tables()


def submission_to_dict(submission):
    download_url = None
    if submission.upload_key:
        if STORAGE_BACKEND == 's3':
            download_url = s3_client().generate_presigned_url(
                'get_object',
                Params={'Bucket': S3_BUCKET, 'Key': submission.upload_key},
                ExpiresIn=300,
            )
        else:
            download_url = url_for('download_local_upload', key=submission.upload_key)
    return {
        'id': str(submission.id),
        'name': submission.name,
        'email': submission.email,
        'message': submission.message,
        'has_upload': submission.has_upload,
        'download_url': download_url,
        'original_filename': submission.original_filename,
        'created_at': submission.created_at.astimezone(timezone.utc).isoformat(),
    }


@app.before_request
def begin_db_session():
    db_session()


@app.teardown_request
def remove_db_session(exception=None):
    if exception is not None:
        db_session.rollback()
    db_session.remove()


# =============================================================================
# CORS Configuration
# =============================================================================

cors = CORS(
    app,
    resources={
        r'/api/*': {
            'origins': [ALLOWED_ORIGIN],
            'methods': ['GET', 'POST', 'DELETE'],
            'allow_headers': ['Authorization', 'Content-Type'],
            'expose_headers': [],
            'supports_credentials': False
        }
    }
)

# =============================================================================
# Rate Limiter Configuration
# =============================================================================

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=[],
    storage_uri='memory://',
    strategy='fixed-window'
)

LOCAL_UPLOAD_DIR = Path(__file__).resolve().parent / 'uploads'
MAX_UPLOAD_SIZE = 10 * 1024 * 1024
_s3_client = None


def s3_client():
    """Create an S3 client using boto3's default credential chain."""
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client('s3', region_name=AWS_REGION)
    return _s3_client

# =============================================================================
# Token Utilities
# =============================================================================

def generate_admin_token():
    """Generate a signed admin token valid for 1 hour."""
    return TOKEN_SERIALIZER.dumps({'admin': True, 'iat': datetime.utcnow().isoformat()})


def validate_token(token):
    """
    Validate a token and check its expiry.
    Returns (valid: bool, data: dict | None)
    Uses hmac.compare_digest for credential comparison where applicable.
    """
    if not token:
        return False, None
    
    try:
        # Load with max_age check (1 hour expiry)
        data = TOKEN_SERIALIZER.loads(
            token,
            max_age=MAX_TOKEN_AGE
        )
        
        # Verify admin flag using hmac.compare_digest
        if not hmac.compare_digest(str(data.get('admin')), 'True'):
            return False, None
        
        return True, data
    except SignatureExpired:
        logger.warning('Token expired', extra={'token': token[:10] + '...' if token else None})
        return False, None
    except BadSignature:
        logger.warning('Invalid token signature', extra={'token': token[:10] + '...' if token else None})
        return False, None
    except Exception as e:
        logger.warning('Token validation error', extra={'error': str(e), 'token': token[:10] + '...' if token else None})
        return False, None

# =============================================================================
# Decorators
# =============================================================================

def require_admin(f):
    """
    Decorator to validate admin tokens for protected routes.
    Expects Authorization: Bearer <token> header.
    Returns 401 with JSON on invalid/expired tokens.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Get Authorization header
        auth_header = request.headers.get('Authorization', '')
        
        if not auth_header.startswith('Bearer '):
            logger.warning('Missing or invalid Authorization header', 
                         extra={'path': request.path, 'headers': dict(request.headers)})
            return jsonify({'error': 'Unauthorized', 'message': 'Bearer token required'}), 401
        
        token = auth_header.split(' ', 1)[1].strip()
        
        valid, data = validate_token(token)
        
        if not valid:
            logger.warning('Token validation failed', 
                         extra={'path': request.path, 'token': token[:10] + '...'})
            return jsonify({'error': 'Unauthorized', 'message': 'Invalid or expired token'}), 401
        
        return f(*args, **kwargs)
    
    return decorated_function


# =============================================================================
# Database Connectivity Check
# =============================================================================

def check_db_connectivity():
    """Check if the database is accessible."""
    try:
        with engine.connect() as conn:
            conn.execute(text('SELECT 1'))
        return True
    except Exception as e:
        logger.error('Database connectivity check failed', extra={'error': str(e)})
        return False


# =============================================================================
# Routes
# =============================================================================

# Health check endpoint
@app.route('/api/health', methods=['GET'])
def health_check():
    """Return health status and check DB connectivity."""
    db_ok = check_db_connectivity()
    
    status = 'ok' if db_ok else 'degraded'
    
    result = {
        'status': status,
        'timestamp': datetime.utcnow().isoformat() + 'Z'
    }
    
    if not db_ok:
        result['database'] = 'unavailable'
    else:
        result['database'] = 'ok'
    
    # Return 503 if DB is down
    if status == 'degraded':
        return jsonify(result), 503
    
    return jsonify(result), 200


# Admin login (no auth required, creates token)
@app.route('/api/admin/login', methods=['POST'])
@limiter.limit('5 per minute')
def admin_login():
    """Admin login endpoint. Returns a signed token."""
    # In production, validate credentials here
    # For hardening demo, we just return a token
    
    # Get credentials from request (example)
    data = request.get_json(silent=True) or {}
    username = data.get('username', '')
    password = data.get('password', '')
    
    # Validate against known admin credentials from configuration
    # Use hmac.compare_digest for credential comparison
    # Compare username
    if not hmac.compare_digest(username, ADMIN_USERNAME):
        logger.warning('Admin login failed: invalid username', 
                     extra={'username': username})
        return jsonify({'error': 'Unauthorized', 'message': 'Invalid credentials'}), 401
    
    # Compare password
    if not hmac.compare_digest(password, ADMIN_PASSWORD):
        logger.warning('Admin login failed: invalid password', 
                     extra={'username': username})
        return jsonify({'error': 'Unauthorized', 'message': 'Invalid credentials'}), 401
    
    token = generate_admin_token()
    
    logger.info('Admin login successful', extra={'username': username})
    
    return jsonify({
        'status': 'ok',
        'token': token,
        'expires_in': MAX_TOKEN_AGE
    }), 200


# Protected admin routes
@app.route('/api/admin/dashboard', methods=['GET'])
@require_admin
def admin_dashboard():
    """Example protected admin route."""
    return jsonify({'status': 'ok', 'message': 'Admin dashboard accessed'}), 200


@app.route('/api/admin/users', methods=['GET'])
@require_admin
def admin_users():
    """Example protected admin route."""
    return jsonify({'status': 'ok', 'users': []}), 200


@app.route('/api/admin/config', methods=['GET', 'POST', 'DELETE'])
@require_admin
def admin_config():
    """Example protected admin route with multiple methods."""
    if request.method == 'GET':
        return jsonify({'status': 'ok', 'config': {}}), 200
    elif request.method == 'POST':
        return jsonify({'status': 'ok', 'message': 'Config updated'}), 200
    elif request.method == 'DELETE':
        return jsonify({'status': 'ok', 'message': 'Config deleted'}), 200


# Rate-limited public endpoints
@app.route('/api/contact', methods=['POST'])
@limiter.limit('10 per minute')
def contact():
    """Contact endpoint with rate limiting."""
    data = request.get_json(silent=True) or {}
    required_fields = ('name', 'email', 'message')
    if any(not isinstance(data.get(field), str) or not data[field].strip() for field in required_fields):
        return jsonify({
            'error': 'Bad Request',
            'message': 'name, email, and message are required',
        }), 400

    submission = Submission(
        name=data['name'].strip(),
        email=data['email'].strip(),
        message=data['message'].strip(),
        has_upload=bool(data.get('has_upload', False)),
        upload_key=data.get('upload_key'),
        original_filename=data.get('original_filename'),
    )
    db_session.add(submission)
    db_session.commit()
    return jsonify({
        'status': 'ok',
        'message': 'Contact received',
        'id': str(submission.id),
    }), 201


@app.route('/api/admin/submissions', methods=['GET'])
@require_admin
def admin_submissions():
    """Return all contact submissions, newest first."""
    submissions = db_session.scalars(
        select(Submission).order_by(Submission.created_at.desc())
    ).all()
    return jsonify({'status': 'ok', 'submissions': [submission_to_dict(item) for item in submissions]}), 200


@app.route('/api/admin/submissions/<submission_id>', methods=['DELETE'])
@require_admin
def delete_submission(submission_id):
    """Delete an uploaded object, if present, and its submission record."""
    try:
        parsed_id = uuid.UUID(submission_id)
    except (ValueError, TypeError):
        return jsonify({'error': 'Bad Request', 'message': 'Invalid submission id'}), 400

    submission = db_session.get(Submission, parsed_id)
    if submission is None:
        return jsonify({'error': 'Not Found', 'message': 'Submission not found'}), 404

    if submission.upload_key:
        if STORAGE_BACKEND == 's3':
            s3_client().delete_object(Bucket=S3_BUCKET, Key=submission.upload_key)
        else:
            local_path = (LOCAL_UPLOAD_DIR.parent / submission.upload_key).resolve()
            if LOCAL_UPLOAD_DIR.resolve() not in local_path.parents:
                return jsonify({'error': 'Internal Server Error', 'message': 'Invalid stored upload key'}), 500
            local_path.unlink(missing_ok=True)

    db_session.delete(submission)
    db_session.commit()
    return jsonify({'status': 'ok', 'message': 'Submission deleted'}), 200


@app.route('/api/upload-doc', methods=['POST'])
@limiter.limit('10 per minute')
def upload_doc():
    """Document upload endpoint with rate limiting."""
    uploaded = request.files.get('file')
    if uploaded is None or not uploaded.filename:
        return jsonify({'error': 'Bad Request', 'message': 'A PDF file is required'}), 400

    original_filename = uploaded.filename
    if not original_filename.lower().endswith('.pdf'):
        return jsonify({'error': 'Bad Request', 'message': 'Only .pdf files are allowed'}), 400

    safe_filename = secure_filename(original_filename)
    if not safe_filename:
        return jsonify({'error': 'Bad Request', 'message': 'Invalid filename'}), 400

    # Read at most one byte beyond the limit so chunked uploads are bounded too.
    content = uploaded.stream.read(MAX_UPLOAD_SIZE + 1)
    if len(content) > MAX_UPLOAD_SIZE:
        return jsonify({'error': 'Payload Too Large', 'message': 'File exceeds 10 MB'}), 413
    if not content.startswith(b'%PDF-'):
        return jsonify({'error': 'Bad Request', 'message': 'File content is not a PDF'}), 400

    object_key = f'uploads/{uuid.uuid4()}.pdf'
    if STORAGE_BACKEND == 's3':
        s3_client().put_object(
            Bucket=S3_BUCKET,
            Key=object_key,
            Body=content,
            ServerSideEncryption='AES256',
            ContentType='application/pdf',
            Metadata={'original-filename': safe_filename},
        )
    else:
        target = LOCAL_UPLOAD_DIR.parent / object_key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    return jsonify({
        'status': 'ok',
        'message': 'Document uploaded',
        'upload_key': object_key,
        'original_filename': original_filename,
    }), 201


@app.route('/api/admin/uploads/<path:key>', methods=['GET'])
@require_admin
def download_local_upload(key):
    """Serve local-development uploads to authenticated admins."""
    if STORAGE_BACKEND != 'local':
        return jsonify({'error': 'Not Found', 'message': 'Endpoint not found'}), 404
    path = (LOCAL_UPLOAD_DIR.parent / key).resolve()
    if LOCAL_UPLOAD_DIR.resolve() not in path.parents or not path.is_file():
        return jsonify({'error': 'Not Found', 'message': 'Upload not found'}), 404
    return send_file(path, mimetype='application/pdf', as_attachment=True)


# =============================================================================
# Error Handlers
# =============================================================================

@app.errorhandler(401)
def unauthorized_error(error):
    """Return JSON for 401 errors."""
    return jsonify({'error': 'Unauthorized', 'message': 'Authentication required'}), 401


@app.errorhandler(404)
def not_found_error(error):
    """Return JSON for 404 errors."""
    return jsonify({'error': 'Not Found', 'message': 'Endpoint not found'}), 404


@app.errorhandler(429)
def ratelimit_handler(error):
    """Return JSON for rate limit exceeded."""
    return jsonify({
        'error': 'Too Many Requests',
        'message': 'Rate limit exceeded',
        'retry_after': error.description
    }), 429


@app.errorhandler(413)
def content_too_large(error):
    """Return JSON for payload too large."""
    return jsonify({
        'error': 'Payload Too Large',
        'message': 'Request exceeds maximum content length (10 MB)'
    }), 413


@app.errorhandler(500)
def internal_error(error):
    """Return JSON for 500 errors."""
    logger.error('Internal server error', extra={'error': str(error)})
    return jsonify({'error': 'Internal Server Error', 'message': 'An unexpected error occurred'}), 500


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == '__main__':
    # This is for development only; production uses gunicorn
    logger.warning('Running in development mode. Use gunicorn for production.')
    create_tables()
    app.run(host='0.0.0.0', port=PORT)
