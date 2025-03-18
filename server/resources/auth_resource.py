import random
import datetime
from flask import url_for, current_app, make_response, redirect
from flask_restful import Resource, reqparse
from models import db, User
from flask_jwt_extended import create_access_token, jwt_required, get_jwt
from extensions import bcrypt
from blacklist import BLACKLIST
from oauth_setup import google
import resend
import uuid

# Helper function to generate a 6-digit OTP
def generate_otp():
    return random.randint(100000, 999999)

# Signup Resource with OTP functionality
class SignupResource(Resource):
    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument('name', type=str, required=True, help="Name cannot be blank!")
        parser.add_argument('email', type=str, required=True, help="Email cannot be blank!")
        parser.add_argument('password', type=str, required=True, help="Password cannot be blank!")
        args = parser.parse_args()

        if User.query.filter_by(email=args['email']).first():
            return {"message": "User with that email already exists"}, 400

        hashed_password = bcrypt.generate_password_hash(args['password']).decode('utcreate tables for e-commerce inventory management systemf-8')
        # Create user with OTP details (ensure your User model has these fields)
        new_user = User(
            name=args['name'],
            email=args['email'],
            password=hashed_password,
            otp_code=generate_otp(),  # store OTP code
            otp_expires_at=datetime.datetime.utcnow() + datetime.timedelta(minutes=10),  # OTP valid for 10 minutes
            is_verified=False  # mark as unverified until OTP is confirmed
        )
        db.session.add(new_user)
        db.session.commit()

        # Send OTP email using Resend
        otp_email_params = {
            "from": "HamaNasi <onboarding@hello.fueldash.net>",
            "to": [args['email']],
            "subject": "Verify Your Email Address",
            "html": f"<p>Your OTP code is <strong>{new_user.otp_code}</strong>. It expires in 10 minutes.</p>"
        }
        try:
            resend.Emails.send(otp_email_params)
        except Exception as e:
            current_app.logger.error(f"Failed to send OTP email: {str(e)}")
            return {"message": "User created but failed to send OTP email"}, 500

        return {"message": "User created. Please verify your email using the OTP sent.", "user_id": new_user.id}, 201

# New Resource to verify OTP
class VerifyOTPResource(Resource):
    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument('email', type=str, required=True, help="Email is required!")
        parser.add_argument('otp', type=int, required=True, help="OTP code is required!")
        args = parser.parse_args()

        user = User.query.filter_by(email=args['email']).first()
        if not user:
            return {"message": "User not found"}, 404

        # Check if OTP has expired
        if datetime.datetime.utcnow() > user.otp_expires_at:
            return {"message": "OTP has expired"}, 400

        # Check if OTP matches
        if user.otp_code != str(args['otp']):
            return {"message": "Invalid OTP code"}, 400

        # Mark user as verified and clear OTP fields
        user.is_verified = True
        user.otp_code = None
        user.otp_expires_at = None
        db.session.commit()

        # Optionally, issue an access token now that the user is verified
        access_token = create_access_token(identity=str(user.id))
        response = make_response({
            "message": "Email verified successfully",
            "access_token": access_token
        }, 200)
        expires = datetime.datetime.now() + datetime.timedelta(days=1)
        response.set_cookie(
            'access_token',
            access_token,
            httponly=True,
            secure=True,
            samesite='None',
            expires=expires,
        )
        return response

# Login Resource 
class LoginResource(Resource):
    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument('email', type=str, required=True, help="Email is required!")
        parser.add_argument('password', type=str, required=True, help="Password is required!")
        parser.add_argument('remember', type=bool, required=False, default=False, help="Remember flag")
        args = parser.parse_args()

        user = User.query.filter_by(email=args['email']).first()
        if not user or not bcrypt.check_password_hash(user.password, args['password']):
            return {"message": "Invalid email or password"}, 401

        # Optionally check if the user's email is verified before login
        if not user.is_verified:
            return {"message": "Email not verified. Please verify your email using the OTP."}, 401

        access_token = create_access_token(identity=str(user.id))
        response = make_response({
            "message": "Login successful",
            "access_token": access_token
        }, 200)

        if args['remember']:
            expires = datetime.datetime.now() + datetime.timedelta(days=30)
            response.set_cookie(
                'access_token',
                access_token,
                httponly=True,
                secure=True,
                samesite='None',
                expires=expires,
            )
        else:
            expires = datetime.datetime.now() + datetime.timedelta(days=1)
            response.set_cookie(
                'access_token',
                access_token,
                httponly=True,
                secure=True,
                samesite='None',
                expires = expires,
            )
        return response

# Logout Resource (unchanged)
class LogoutResource(Resource):
    @jwt_required()
    def post(self):
        jti = get_jwt()["jti"]
        BLACKLIST.add(jti)
        return {"message": "Successfully logged out"}, 200

# Google Login Resources (unchanged)
class LoginGoogle(Resource):
    def get(self):
        try:
            redirect_url = url_for('authorize_google', _external=True)
            return google.authorize_redirect(redirect_url)
        except Exception as e:
            current_app.logger.error(f"Error during Google login: {str(e)}")
            return {"message": "Error occurred during login"}, 500

class AuthorizeGoogle(Resource):
    def get(self):
        try:
            token = google.authorize_access_token()
        except Exception as e:
            current_app.logger.error(f"Error authorizing Google access token: {str(e)}")
            return {"message": "Failed to authorize access token", "error": str(e)}, 400

        userinfo_endpoint = google.server_metadata.get('userinfo_endpoint')
        if not userinfo_endpoint:
            return {"message": "Userinfo endpoint not available"}, 500

        res = google.get(userinfo_endpoint)
        user_info = res.json()
        email = user_info.get('email')
        if not email:
            return {"message": "Email not found in user info"}, 400

        user = User.query.filter_by(email=email).first()
        if not user:
            user = User(
                name=user_info.get('name', email),
                email=email,
                password='',
                is_verified=True
            )
            db.session.add(user)
            db.session.commit()

        access_token = create_access_token(identity=str(user.id))
        response = make_response(redirect("https://hama-nasi.vercel.app/onboarding"))

        expires = datetime.datetime.now() + datetime.timedelta(days=1)
        response.set_cookie(
            'access_token',
            access_token,
            httponly=True,
            secure=True,
            samesite='None',
            expires=expires,
        )
        return response


class ResendOTPResource(Resource):
    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument('email', type=str, required=True, help="Email is required!")
        args = parser.parse_args()

        user = User.query.filter_by(email=args['email']).first()
        if not user:
            return {"message": "User not found"}, 404

        # If the user is already verified, there's no need to resend an OTP.
        if user.is_verified:
            return {"message": "Email is already verified"}, 400

        # Generate a new OTP and update expiration time (10 minutes from now)
        new_otp = generate_otp()  # Helper function defined earlier
        user.otp_code = new_otp
        user.otp_expires_at = datetime.datetime.utcnow() + datetime.timedelta(minutes=10)
        db.session.commit()

        # Prepare the OTP email parameters
        otp_email_params = {
            "from": "HamaNasi <onboarding@hello.fueldash.net>",
            "to": [args['email']],
            "subject": "Verify Your Email Address",
            "html": f"<p>Your new OTP code is <strong>{new_otp}</strong>. It expires in 10 minutes.</p>"
        }

        # Attempt to send the OTP email
        try:
            resend.Emails.send(otp_email_params)
        except Exception as e:
            current_app.logger.error(f"Failed to resend OTP email: {str(e)}")
            return {"message": "Failed to resend OTP email"}, 500

        return {"message": "OTP resent successfully. Please check your email."}, 200

class ForgotPasswordResource(Resource):
    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument("email", type=str, required=True, help="Email is required!")
        args = parser.parse_args()

        user = User.query.filter_by(email=args["email"]).first()
        if not user:
            return {"message": "User not found"}, 404

        # Generate a unique reset token and expiration (30 minutes from now)
        reset_token = uuid.uuid4().hex
        reset_expires_at = datetime.datetime.utcnow() + datetime.timedelta(minutes=30)

        user.reset_token = reset_token
        user.reset_expires_at = reset_expires_at
        db.session.commit()

        # Build a password reset link pointing to the frontend page.
        reset_link = f"http://localhost:3000/new-password?token={reset_token}"
        reset_email_params = {
            "from": "Hama Nasi <onboarding@hello.fueldash.net>",
            "to": [args["email"]],
            "subject": "Reset Your Password",
            "html": f"<p>To reset your password, click the following link: <a href='{reset_link}'>{reset_link}</a>. This link expires in 30 minutes.</p>"
        }
        try:
            resend.Emails.send(reset_email_params)
        except Exception as e:
            current_app.logger.error(f"Failed to send reset password email: {str(e)}")
            return {"message": "Failed to send reset password email"}, 500

        return {"message": "Reset password email sent successfully"}, 200


class ResetPasswordResource(Resource):
    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument("reset_token", type=str, required=True, help="Reset token is required!")
        parser.add_argument("new_password", type=str, required=True, help="New password is required!")
        args = parser.parse_args()

        user = User.query.filter_by(reset_token=args["reset_token"]).first()
        if not user:
            return {"message": "Invalid or expired reset token"}, 400

        # Check if the token has expired
        if datetime.datetime.utcnow() > user.reset_expires_at:
            return {"message": "Reset token has expired"}, 400

        # Update the password (hash the new password)
        hashed_password = bcrypt.generate_password_hash(args["new_password"]).decode("utf-8")
        user.password = hashed_password

        # Clear the reset token fields
        user.reset_token = None
        user.reset_expires_at = None
        db.session.commit()

        return {"message": "Password reset successful"}, 200


