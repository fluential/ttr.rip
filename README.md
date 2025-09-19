## Authentication
How Authentication Works (Overall Flow)

The application has two distinct authentication mechanisms:

 1 API Authentication (JWT-based): This is the primary, secure method used by the frontend JavaScript to communicate with the backend API (/api/v1/*).
 2 Web UI Authentication (Cookie-based): This is a simpler mechanism used only to control access to the HTML dashboard page itself.

The flow for the API is as follows:

 1 When the dashboard page (/) loads, the JavaScript in dashboard.html immediately calls the loginAndGetToken() function.
 2 This function sends a POST request to /api/v1/token with the hardcoded credentials username: 'admin' and password: 'password'.
 3 The /api/v1/token endpoint (in app/api/v1/endpoints/login.py) verifies these credentials against the user in the database.
 4 If the credentials are correct, it generates a JSON Web Token (JWT) and returns it to the browser.
 5 The JavaScript stores this JWT in a variable (apiToken).
 6 For all subsequent API requests (like fetching or creating checks), the JavaScript includes this token in the Authorization header, like so: Authorization: Bearer <the_jwt_token>.
 7 The API endpoints for checks (in app/api/v1/endpoints/checks.py) are protected and use a dependency to validate this token on every request.

How JWT is Used and Validated

JWT Creation:

 • The creation happens in app/security.py within the create_access_token function.
 • When a user logs in successfully via the /api/v1/token endpoint, this function is called.
 • It creates a Python dictionary (the "payload") containing the user's username (as sub, a standard JWT claim for "subject") and an expiration timestamp (exp).
 • It then uses the jose.jwt.encode() method to sign this payload. The signing process uses the SECRET_KEY and the ALGORITHM (HS256) defined in app/core/config.py.
 • The result is the compact, signed JWT string that is sent back to the client.

JWT Validation:

 • Validation happens in app/security.py inside the get_current_user function, which acts as a FastAPI dependency.
 • Protected API endpoints, like read_checks in app/api/v1/endpoints/checks.py, include this function in their signature: current_user: db_models.User = Depends(security.get_current_user).
 • FastAPI automatically extracts the token from the Authorization: Bearer ... header.
 • The get_current_user function then uses jose.jwt.decode() to verify and decode the token. This process uses the same SECRET_KEY and ALGORITHM to check the token's signature and ensure it hasn't been tampered
   with. It also automatically checks if the token has expired.
 • If the token is valid, the function extracts the username from the payload, fetches the corresponding user from the database, and returns the user object.
 • If the token is invalid, expired, or the signature doesn't match, a 401 Unauthorized HTTP exception is raised, and the request is denied.

Where the Validation Keys are Stored

The application uses a symmetric algorithm (HS256), which means it uses a single secret key for both signing and validating tokens, not a public/private key pair.

This secret key is managed in app/core/config.py:

```python
# app/core/config.py

class Settings(BaseSettings):
    # ...
    SECRET_KEY: str = "a_very_secret_key"
    ALGORITHM: str = "HS256"
    # ...

    model_config = SettingsConfigDict(env_file=".env")

settings = Settings()
```

The value is loaded from environment variables. It has a default value of "a_very_secret_key" for development but is intended to be overridden in production by setting a SECRET_KEY environment variable or placing
it in a .env file, as shown in .env.example.
