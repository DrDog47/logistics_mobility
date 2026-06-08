"""Flask extension instances.

Created here (not bound to app) so they can be imported anywhere without
triggering circular imports. Bound to the app inside the factory.
"""

from flask_babel import Babel
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
babel = Babel()
csrf = CSRFProtect()
