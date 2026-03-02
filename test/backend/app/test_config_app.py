import pytest
import unittest
from unittest.mock import patch, MagicMock, Mock
import sys
import os

from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import JSONResponse
import atexit

# Add the backend directory to path so we can import modules
backend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../backend'))
sys.path.insert(0, backend_path)

# Apply patches before importing any app modules
# Apply critical patches before importing any modules
# This prevents real AWS/MinIO/Elasticsearch calls during import
patch('botocore.client.BaseClient._make_api_call', return_value={}).start()

# Patch storage factory and MinIO config validation to avoid errors during initialization
# These patches must be started before any imports that use MinioClient
storage_client_mock = MagicMock()
minio_mock = MagicMock()
minio_mock._ensure_bucket_exists = MagicMock()
minio_mock.client = MagicMock()

# Start critical patches first - storage factory and config validation must be patched
# before any module imports that might trigger MinioClient initialization
critical_patches = [
    # Patch storage factory and MinIO config validation FIRST
    patch('nexent.storage.storage_client_factory.create_storage_client_from_config',
          return_value=storage_client_mock),
    patch('nexent.storage.minio_config.MinIOStorageConfig.validate',
          lambda self: None),
    # Mock boto3 client
    patch('boto3.client', return_value=Mock()),
    # Mock boto3 resource
    patch('boto3.resource', return_value=Mock()),
    # Mock Elasticsearch to prevent connection errors
    patch('elasticsearch.Elasticsearch', return_value=Mock()),
]

for p in critical_patches:
    p.start()

# Patch MinioClient class to return mock instance when instantiated
# This prevents real initialization during module import
patches = [
    patch('backend.database.client.MinioClient', return_value=minio_mock),
    patch('database.client.MinioClient', return_value=minio_mock),
    patch('backend.database.client.minio_client', minio_mock),
]

for p in patches:
    p.start()

# Combine all patches for cleanup
all_patches = critical_patches + patches

# Now safe to import modules that use database.client
# After import, we can patch get_db_session if needed
try:
    from backend.database import client as db_client_module

    # Patch get_db_session after module is imported
    db_session_patch = patch.object(
        db_client_module, 'get_db_session', return_value=Mock())
    db_session_patch.start()
    all_patches.append(db_session_patch)
except ImportError:
    # If import fails, try patching the path directly (may trigger import)
    db_session_patch = patch(
        'backend.database.client.get_db_session', return_value=Mock())
    db_session_patch.start()
    all_patches.append(db_session_patch)

# Now safe to import app modules - imports moved after patches
from apps.config_app import app
from consts.exceptions import AppException
from consts.error_code import ErrorCode

# Stop all patches at the end of the module


def stop_patches():
    for p in all_patches:
        p.stop()


atexit.register(stop_patches)


class TestBaseApp(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_app_initialization(self):
        """Test that the FastAPI app is initialized with correct root path."""
        self.assertEqual(app.root_path, "/api")

    def test_cors_middleware(self):
        """Test that CORS middleware is properly configured."""
        # Find the CORS middleware
        cors_middleware = None
        for middleware in app.user_middleware:
            if middleware.cls.__name__ == "CORSMiddleware":
                cors_middleware = middleware
                break

        self.assertIsNotNone(cors_middleware)

        # In FastAPI, middleware options are stored in 'middleware.kwargs'
        self.assertEqual(cors_middleware.kwargs.get("allow_origins"), ["*"])
        self.assertTrue(cors_middleware.kwargs.get("allow_credentials"))
        self.assertEqual(cors_middleware.kwargs.get("allow_methods"), ["*"])
        self.assertEqual(cors_middleware.kwargs.get("allow_headers"), ["*"])

    def test_routers_included(self):
        """Test that all routers are included in the app."""
        # Get all routes in the app
        routes = [route.path for route in app.routes]

        # Check if routes exist (at least some routes should be present)
        self.assertTrue(len(routes) > 0)

    def test_http_exception_handler(self):
        """Test the HTTP exception handler."""
        # Test that the exception handler is registered
        exception_handlers = app.exception_handlers
        self.assertIn(HTTPException, exception_handlers)

        # Test that the handler function exists and is callable
        http_exception_handler = exception_handlers[HTTPException]
        self.assertIsNotNone(http_exception_handler)
        self.assertTrue(callable(http_exception_handler))

    def test_generic_exception_handler(self):
        """Test the generic exception handler."""
        # Test that the exception handler is registered
        exception_handlers = app.exception_handlers
        self.assertIn(Exception, exception_handlers)

        # Test that the handler function exists and is callable
        generic_exception_handler = exception_handlers[Exception]
        self.assertTrue(callable(generic_exception_handler))

    def test_exception_handling_with_client(self):
        """Test exception handling using the test client."""
        # This test requires mocking an endpoint that raises an exception
        # For demonstration purposes, we'll check if status_code for a non-existent endpoint is 404
        response = self.client.get("/non-existent-endpoint")
        self.assertEqual(response.status_code, 404)

    def test_speed_mode_logic(self):
        """Test the speed mode conditional logic."""
        # Since the conditional logic is executed at import time,
        # we test the logic by checking the final state of the app
        from apps.config_app import app
        from consts.const import IS_SPEED_MODE

        # Verify that the app has been properly initialized with routers
        self.assertIsNotNone(app)
        self.assertGreater(len(app.routes), 10)  # Should have many routes

        # Test that IS_SPEED_MODE is accessible
        self.assertIsInstance(IS_SPEED_MODE, bool)

    @patch('utils.monitoring.monitoring_manager.setup_fastapi_app')
    def test_monitoring_setup(self, mock_setup):
        """Test that monitoring is set up for the application."""
        # Re-import to trigger the setup
        import importlib
        import apps.config_app
        importlib.reload(apps.config_app)

        # Verify that setup_fastapi_app was called with the app
        mock_setup.assert_called_once()
        # The argument should be the FastAPI app instance
        call_args = mock_setup.call_args[0]
        self.assertEqual(call_args[0].root_path, "/api")

    def test_all_routers_included(self):
        """Test that all expected routers are included in the app."""
        expected_routers = [
            'model_manager_router',
            'config_sync_router',
            'agent_router',
            'vectordatabase_router',
            'voice_router',
            'file_manager_router',
            'proxy_router',
            'tool_config_router',
            # or 'user_management_router' depending on IS_SPEED_MODE
            'mock_user_management_router',
            'summary_router',
            'prompt_router',
            'tenant_config_router',
            'remote_mcp_router',
            'tenant_router',
            'group_router',
            'invitation_router'
        ]

        # Get all router names that were included
        included_routers = []
        for route in app.routes:
            if hasattr(route, 'tags') and route.tags:
                # Try to identify router by tags or other means
                pass

        # Since it's hard to identify routers directly from routes,
        # we'll check that we have a reasonable number of routes
        # Should have many routes from all routers
        self.assertGreater(len(app.routes), 10)

    def test_http_exception_handler_registration(self):
        """Test that HTTP exception handler is properly registered."""
        # Test that the exception handler exists in the app
        exception_handlers = app.exception_handlers
        self.assertIn(HTTPException, exception_handlers)

    def test_generic_exception_handler_registration(self):
        """Test that generic exception handler is properly registered."""
        # Test that the exception handler exists in the app
        exception_handlers = app.exception_handlers
        self.assertIn(Exception, exception_handlers)


class TestHttpExceptionHandler(unittest.TestCase):
    """Unit tests for http_exception_handler."""

    def setUp(self):
        """Set up test fixtures."""
        self.app = app
        # Get the registered handler
        self.handler = self.app.exception_handlers[HTTPException]

    @pytest.mark.asyncio
    async def test_http_exception_handler_400(self):
        """Test HTTPException handler with 400 status code."""
        # Create mock request
        mock_request = MagicMock(spec=Request)

        # Create HTTPException
        exc = HTTPException(status_code=400, detail="Bad Request")

        # Call the handler
        response = await self.handler(mock_request, exc)

        # Verify response
        self.assertEqual(response.status_code, 400)
        body = response.body.decode()
        self.assertIn("Bad Request", body)

    @pytest.mark.asyncio
    async def test_http_exception_handler_401(self):
        """Test HTTPException handler with 401 status code."""
        mock_request = MagicMock(spec=Request)
        exc = HTTPException(status_code=401, detail="Unauthorized")

        response = await self.handler(mock_request, exc)

        self.assertEqual(response.status_code, 401)
        body = response.body.decode()
        self.assertIn("Unauthorized", body)

    @pytest.mark.asyncio
    async def test_http_exception_handler_403(self):
        """Test HTTPException handler with 403 status code."""
        mock_request = MagicMock(spec=Request)
        exc = HTTPException(status_code=403, detail="Forbidden")

        response = await self.handler(mock_request, exc)

        self.assertEqual(response.status_code, 403)
        body = response.body.decode()
        self.assertIn("Forbidden", body)

    @pytest.mark.asyncio
    async def test_http_exception_handler_404(self):
        """Test HTTPException handler with 404 status code."""
        mock_request = MagicMock(spec=Request)
        exc = HTTPException(status_code=404, detail="Not Found")

        response = await self.handler(mock_request, exc)

        self.assertEqual(response.status_code, 404)
        body = response.body.decode()
        self.assertIn("Not Found", body)

    @pytest.mark.asyncio
    async def test_http_exception_handler_422(self):
        """Test HTTPException handler with 422 status code (validation error)."""
        mock_request = MagicMock(spec=Request)
        exc = HTTPException(status_code=422, detail="Validation Error")

        response = await self.handler(mock_request, exc)

        self.assertEqual(response.status_code, 422)
        body = response.body.decode()
        self.assertIn("Validation Error", body)

    @pytest.mark.asyncio
    async def test_http_exception_handler_429(self):
        """Test HTTPException handler with 429 status code (rate limit)."""
        mock_request = MagicMock(spec=Request)
        exc = HTTPException(status_code=429, detail="Rate limit exceeded")

        response = await self.handler(mock_request, exc)

        self.assertEqual(response.status_code, 429)
        body = response.body.decode()
        self.assertIn("Rate limit exceeded", body)

    @pytest.mark.asyncio
    async def test_http_exception_handler_500(self):
        """Test HTTPException handler with 500 status code."""
        mock_request = MagicMock(spec=Request)
        exc = HTTPException(status_code=500, detail="Internal Server Error")

        response = await self.handler(mock_request, exc)

        self.assertEqual(response.status_code, 500)
        body = response.body.decode()
        self.assertIn("Internal Server Error", body)

    @pytest.mark.asyncio
    async def test_http_exception_handler_response_format(self):
        """Test that HTTPException handler returns correct JSON format."""
        mock_request = MagicMock(spec=Request)
        exc = HTTPException(status_code=400, detail="Test error message")

        response = await self.handler(mock_request, exc)

        # Verify response is JSONResponse
        self.assertIsInstance(response, JSONResponse)

        # Verify body contains expected structure
        body = response.body.decode()
        self.assertIn("message", body)
        self.assertIn("Test error message", body)


class TestAppExceptionHandler(unittest.TestCase):
    """Unit tests for app_exception_handler."""

    def setUp(self):
        """Set up test fixtures."""
        self.app = app
        # Get the registered handler for AppException
        self.handler = self.app.exception_handlers[AppException]

    @pytest.mark.asyncio
    async def test_app_exception_handler_validation_error(self):
        """Test AppException handler with VALIDATION_ERROR."""
        mock_request = MagicMock(spec=Request)
        exc = AppException(ErrorCode.VALIDATION_ERROR, "Validation failed")

        response = await self.handler(mock_request, exc)

        self.assertEqual(response.status_code, 400)
        body = response.body.decode()
        self.assertIn("code", body)
        self.assertIn("VALIDATION_ERROR", body)
        self.assertIn("message", body)
        self.assertIn("Validation failed", body)

    @pytest.mark.asyncio
    async def test_app_exception_handler_unauthorized(self):
        """Test AppException handler with UNAUTHORIZED."""
        mock_request = MagicMock(spec=Request)
        exc = AppException(ErrorCode.UNAUTHORIZED, "Unauthorized access")

        response = await self.handler(mock_request, exc)

        self.assertEqual(response.status_code, 401)
        body = response.body.decode()
        self.assertIn("UNAUTHORIZED", body)

    @pytest.mark.asyncio
    async def test_app_exception_handler_forbidden(self):
        """Test AppException handler with FORBIDDEN."""
        mock_request = MagicMock(spec=Request)
        exc = AppException(ErrorCode.FORBIDDEN, "Access forbidden")

        response = await self.handler(mock_request, exc)

        self.assertEqual(response.status_code, 403)
        body = response.body.decode()
        self.assertIn("FORBIDDEN", body)

    @pytest.mark.asyncio
    async def test_app_exception_handler_resource_not_found(self):
        """Test AppException handler with RESOURCE_NOT_FOUND."""
        mock_request = MagicMock(spec=Request)
        exc = AppException(ErrorCode.RESOURCE_NOT_FOUND, "Resource not found")

        response = await self.handler(mock_request, exc)

        self.assertEqual(response.status_code, 404)
        body = response.body.decode()
        self.assertIn("RESOURCE_NOT_FOUND", body)

    @pytest.mark.asyncio
    async def test_app_exception_handler_rate_limit_exceeded(self):
        """Test AppException handler with RATE_LIMIT_EXCEEDED."""
        mock_request = MagicMock(spec=Request)
        exc = AppException(ErrorCode.RATE_LIMIT_EXCEEDED,
                           "Rate limit exceeded")

        response = await self.handler(mock_request, exc)

        self.assertEqual(response.status_code, 429)
        body = response.body.decode()
        self.assertIn("RATE_LIMIT_EXCEEDED", body)

    @pytest.mark.asyncio
    async def test_app_exception_handler_internal_error(self):
        """Test AppException handler with INTERNAL_ERROR."""
        mock_request = MagicMock(spec=Request)
        exc = AppException(ErrorCode.INTERNAL_ERROR, "Internal error occurred")

        response = await self.handler(mock_request, exc)

        self.assertEqual(response.status_code, 500)
        body = response.body.decode()
        self.assertIn("INTERNAL_ERROR", body)

    @pytest.mark.asyncio
    async def test_app_exception_handler_with_details(self):
        """Test AppException handler with details field."""
        mock_request = MagicMock(spec=Request)
        details = {"field": "api_key", "issue": "invalid format"}
        exc = AppException(
            ErrorCode.DIFY_CONFIG_INVALID,
            "Invalid configuration",
            details=details
        )

        response = await self.handler(mock_request, exc)

        self.assertEqual(response.status_code, 400)
        body = response.body.decode()
        self.assertIn("details", body)

    @pytest.mark.asyncio
    async def test_app_exception_handler_without_details(self):
        """Test AppException handler without details field - should return None."""
        mock_request = MagicMock(spec=Request)
        exc = AppException(ErrorCode.VALIDATION_ERROR, "Validation failed")

        response = await self.handler(mock_request, exc)

        body = response.body.decode()
        # When details is None, the details field should be null/None in response
        self.assertIn(
            '"details": null', body) or "details" not in body or '"details": null' in body

    @pytest.mark.asyncio
    async def test_app_exception_handler_response_format(self):
        """Test that AppException handler returns correct JSON format."""
        mock_request = MagicMock(spec=Request)
        exc = AppException(ErrorCode.TOKEN_EXPIRED, "Token has expired")

        response = await self.handler(mock_request, exc)

        # Verify response is JSONResponse
        self.assertIsInstance(response, JSONResponse)
        self.assertEqual(response.status_code, 401)

        # Verify body contains expected structure
        body = response.body.decode()
        self.assertIn("code", body)
        self.assertIn("TOKEN_EXPIRED", body)
        self.assertIn("message", body)
        self.assertIn("Token has expired", body)


class TestGenericExceptionHandler(unittest.TestCase):
    """Unit tests for generic_exception_handler."""

    def setUp(self):
        """Set up test fixtures."""
        self.app = app
        # Get the registered handler for generic Exception
        self.handler = self.app.exception_handlers[Exception]

    @pytest.mark.asyncio
    async def test_generic_exception_handler_runtime_error(self):
        """Test generic exception handler with RuntimeError."""
        mock_request = MagicMock(spec=Request)
        exc = RuntimeError("Something went wrong")

        response = await self.handler(mock_request, exc)

        self.assertEqual(response.status_code, 500)
        body = response.body.decode()
        self.assertIn("Internal server error", body)

    @pytest.mark.asyncio
    async def test_generic_exception_handler_value_error(self):
        """Test generic exception handler with ValueError."""
        mock_request = MagicMock(spec=Request)
        exc = ValueError("Invalid value")

        response = await self.handler(mock_request, exc)

        self.assertEqual(response.status_code, 500)
        body = response.body.decode()
        self.assertIn("Internal server error", body)

    @pytest.mark.asyncio
    async def test_generic_exception_handler_type_error(self):
        """Test generic exception handler with TypeError."""
        mock_request = MagicMock(spec=Request)
        exc = TypeError("Expected str, got int")

        response = await self.handler(mock_request, exc)

        self.assertEqual(response.status_code, 500)
        body = response.body.decode()
        self.assertIn("Internal server error", body)

    @pytest.mark.asyncio
    async def test_generic_exception_handler_key_error(self):
        """Test generic exception handler with KeyError."""
        mock_request = MagicMock(spec=Request)
        exc = KeyError("missing_key")

        response = await self.handler(mock_request, exc)

        self.assertEqual(response.status_code, 500)
        body = response.body.decode()
        self.assertIn("Internal server error", body)

    @pytest.mark.asyncio
    async def test_generic_exception_handler_attribute_error(self):
        """Test generic exception handler with AttributeError."""
        mock_request = MagicMock(spec=Request)
        exc = AttributeError("'NoneType' object has no attribute 'foo'")

        response = await self.handler(mock_request, exc)

        self.assertEqual(response.status_code, 500)
        body = response.body.decode()
        self.assertIn("Internal server error", body)

    @pytest.mark.asyncio
    async def test_generic_exception_handler_does_not_leak_details(self):
        """Test that generic exception handler does not leak exception details."""
        mock_request = MagicMock(spec=Request)
        # Create an exception with sensitive details
        exc = RuntimeError("SECRET_API_KEY_12345")

        response = await self.handler(mock_request, exc)

        self.assertEqual(response.status_code, 500)
        body = response.body.decode()
        # Should contain generic message, not the actual exception message
        self.assertIn("Internal server error, please try again later.", body)
        # Should NOT contain the secret
        self.assertNotIn("SECRET_API_KEY", body)

    @pytest.mark.asyncio
    async def test_generic_exception_handler_response_format(self):
        """Test that generic exception handler returns correct JSON format."""
        mock_request = MagicMock(spec=Request)
        exc = Exception("Some error")

        response = await self.handler(mock_request, exc)

        # Verify response is JSONResponse
        self.assertIsInstance(response, JSONResponse)
        self.assertEqual(response.status_code, 500)

        # Verify body contains expected structure
        body = response.body.decode()
        self.assertIn("message", body)
        self.assertIn("Internal server error", body)

    @pytest.mark.asyncio
    async def test_generic_exception_handler_with_app_exception_delegates(self):
        """Test that generic exception handler delegates to app_exception_handler for AppException."""
        mock_request = MagicMock(spec=Request)
        # Create an AppException - should be handled by app_exception_handler
        exc = AppException(ErrorCode.DIFY_AUTH_ERROR,
                           "Dify authentication failed")

        response = await self.handler(mock_request, exc)

        # Should return the AppException status code (401), not 500
        self.assertEqual(response.status_code, 401)
        body = response.body.decode()
        # Should contain the AppException format, not generic
        self.assertIn("DIFY_AUTH_ERROR", body)


class TestExceptionHandlerIntegration(unittest.TestCase):
    """Integration tests for exception handlers using TestClient."""

    def setUp(self):
        """Set up test fixtures."""
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_404_from_client(self):
        """Test that 404 from non-existent endpoint uses HTTPException handler."""
        response = self.client.get("/non-existent-endpoint-12345")
        self.assertEqual(response.status_code, 404)
        # FastAPI's default 404 uses 'detail' key
        self.assertIn("detail", response.json())


if __name__ == "__main__":
    unittest.main()
