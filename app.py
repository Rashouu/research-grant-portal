"""
Research Grant Portal: Flask web application.

app.py is the web bridge between the browser and the shared logic in
researchgrantportal.py, the same role it plays in the laboratory project.

Localhost: python app.py       then open http://127.0.0.1:5000
Render:    gunicorn app:app
"""
import os
import secrets
from functools import wraps
from io import BytesIO

from flask import Flask, abort, flash, g, redirect, render_template, request, send_file, session, url_for

import researchgrantportal as core

app = Flask(__name__)
secret_key = os.getenv("SECRET_KEY", "")
if not secret_key:
    if core.ON_RENDER:
        raise RuntimeError("SECRET_KEY is not set. Add it under Environment in the Render dashboard.")
    secret_key = "local-development-only"  # localhost only; the live site needs its own SECRET_KEY
app.config.update(
    SECRET_KEY=secret_key,
    MAX_CONTENT_LENGTH=4 * core.MAX_FILE_BYTES,  # one form can carry up to four 5 MB files
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=core.ON_RENDER,  # cookies only over https on the live site
)
app.jinja_env.globals.update(core=core, csrf_token=lambda: session.get("csrf", ""))
app.jinja_env.filters.update(money=core.fmt_money, date=core.fmt_date, stamp=core.fmt_stamp,
                             size=core.fmt_size, plural=core.plural)

core.init_db()  # creates missing tables and the admin account


# --- Request helpers --------------------------------------------------------

def db():
    """One database connection per request; committed at the end, or rolled back after an error."""
    if "db" not in g:
        g.db = core.Database()
    return g.db


@app.teardown_request
def close_db(error):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close(commit=error is None)


def new_session(user_id=None):
    """Start a fresh session (after sign-in or sign-out) with a new form token."""
    session.clear()
    session["csrf"] = secrets.token_urlsafe(24)
    if user_id is not None:
        session["user_id"] = user_id


@app.before_request
def load_user_and_check_form():
    if request.endpoint == "static":
        return None
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    g.user = None
    if session.get("user_id"):
        g.user = db().one("SELECT id, full_name, email, role, created_at FROM users "
                          "WHERE id = ? AND is_verified = 1 AND is_locked = 0", (session["user_id"],))
        if g.user is None:
            new_session()
    # Every form carries a hidden token, so other websites cannot submit forms on a user's behalf.
    if request.method == "POST" and not secrets.compare_digest(request.form.get("csrf", ""), session["csrf"]):
        abort(400, "This form has expired. Go back, reload the page and try again.")
    return None


def studies_overview():
    """The signed-in user's studies with their status, worked out once per page."""
    if "studies" not in g:
        g.studies = core.study_items(db(), g.user)
    return g.studies


def uses_default_password(user):
    return core.is_admin(user) and user["email"] == core.LOCAL_ADMIN[1]


@app.context_processor
def sidebar_summary():
    """What the sidebar shows: the studies waiting on this user and a few counts."""
    if not g.get("user"):
        return {}
    return {"rail": core.overview(studies_overview(), g.user)}


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Sign in to continue.", "info")
            return redirect(url_for("index"))
        return view(*args, **kwargs)
    return wrapped


def load_study(study_id):
    """Researchers can open only their own studies; the admin can open all of them."""
    study = core.get_study(db(), study_id)
    if study is None or not (core.is_admin(g.user) or core.is_owner(g.user, study)):
        abort(404)
    return study


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(413)
def show_error(error):
    messages = {
        403: "You do not have access to that page.",
        404: "That page does not exist, or you do not have access to it.",
        413: f"The upload is too large. Each file can be up to {core.MAX_FILE_BYTES // (1024 * 1024)} MB.",
    }
    code = getattr(error, "code", 400)
    return render_template("error.html", code=code, message=messages.get(code, error.description)), code


# --- Sign-in, sign-up and password recovery ----------------------------------

@app.get("/")
def index():
    if g.user:
        return redirect(url_for("home"))
    return render_template("login.html", email="", error=None)


@app.post("/login")
def login():
    email = request.form.get("email", "").strip().lower()
    user, error = core.authenticate(db(), email, request.form.get("password", ""))
    if error == "unverified":
        session["pending_email"] = email
        flash("Confirm your email first: enter the code we sent, or send a new one.", "info")
        return redirect(url_for("verify"))
    if error:
        return render_template("login.html", email=email, error=error), 400
    new_session(user["id"])
    flash(f"Signed in as {user['full_name']}.")
    return redirect(url_for("home"))


@app.post("/logout")
def logout():
    new_session()
    flash("You have signed out.")
    return redirect(url_for("index"))


@app.route("/register", methods=["GET", "POST"])
def register():
    """Sign-up creates researcher accounts only; the admin account comes from ADMIN_EMAIL and ADMIN_PASSWORD."""
    if g.user:
        return redirect(url_for("home"))
    if request.method == "GET":
        return render_template("register.html", form={}, errors={})
    form, errors = core.check(core.RegisterForm, request.form.to_dict())
    if not errors:
        error, note = core.register_researcher(db(), form)
        if error:
            errors = {"email": error}
    if errors:
        return render_template("register.html", form=request.form, errors=errors), 400
    session["pending_email"] = form.email
    flash(note, "info")
    return redirect(url_for("verify"))


@app.route("/verify", methods=["GET", "POST"])
def verify():
    email = session.get("pending_email")
    if not email:
        return redirect(url_for("register"))
    error = None
    if request.method == "POST":
        error = core.verify_registration(db(), email, request.form.get("code", ""))
        if error is None:
            new_session(core.find_user(db(), email)["id"])
            flash("Email verified. Your researcher account is ready.")
            return redirect(url_for("home"))
    return render_template("verify.html", email=email, error=error), 400 if error else 200


@app.post("/verify/resend")
def resend_code():
    email = session.get("pending_email")
    if not email:
        return redirect(url_for("register"))
    error, note = core.resend_registration_code(db(), email)
    flash(error or note, "error" if error else "info")
    return redirect(url_for("verify"))


@app.route("/forgot", methods=["GET", "POST"])
def forgot():
    if request.method == "GET":
        return render_template("forgot.html", email=session.get("reset_email", ""), error=None)
    email = request.form.get("email", "").strip().lower()
    try:
        email = core.valid_email(email)
    except ValueError as exc:
        return render_template("forgot.html", email=email, error=str(exc)), 400
    error = core.start_reset(db(), email)
    if error:
        return render_template("forgot.html", email=email, error=error), 400
    session["reset_email"] = email
    flash(f"If an account uses {email}, a 6-digit code is on its way. It expires in {core.OTP_MINUTES} minutes.", "info")
    return redirect(url_for("reset_password"))


@app.route("/reset", methods=["GET", "POST"])
def reset_password():
    email = session.get("reset_email")
    if not email:
        return redirect(url_for("forgot"))
    errors = {}
    if request.method == "POST":
        form, errors = core.check(core.ResetForm, request.form.to_dict())
        if not errors:
            error = core.finish_reset(db(), email, form)
            if error is None:
                session.pop("reset_email", None)
                flash("Password changed. Sign in with your new password.")
                return redirect(url_for("index"))
            errors = {"code": error}
    return render_template("reset.html", email=email, errors=errors), 400 if errors else 200


@app.route("/account", methods=["GET", "POST"])
@login_required
def account():
    errors = {}
    if request.method == "POST":
        form, errors = core.check(core.ChangePasswordForm, request.form.to_dict())
        if not errors:
            errors = core.change_password(db(), g.user["id"], form)
            if not errors:
                flash("Password changed.")
                return redirect(url_for("account"))
    return render_template("account.html", errors=errors, is_default=uses_default_password(g.user)), 400 if errors else 200


# --- Archive and files ---------------------------------------------------------

@app.get("/archive")
def archive():
    audience = g.user["role"] if g.user else "GUEST"
    query, subject = request.args.get("q", "").strip(), request.args.get("subject", "")
    return render_template("archive.html", audience=audience, query=query, subject=subject,
                           records=core.archive_records(db(), audience, query, subject),
                           total=len(core.archive_records(db(), audience)))


@app.get("/files/<int:file_id>")
@login_required
def download(file_id):
    stored = core.get_file(db(), file_id)
    if stored is None:
        abort(404)
    load_study(stored["study_id"])  # same access rule as the study itself
    inline = stored["content_type"] in ("application/pdf", "image/jpeg", "image/png")
    response = send_file(BytesIO(bytes(stored["data"])), mimetype=stored["content_type"],
                         as_attachment=not inline, download_name=stored["file_name"])
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


# --- Home, dashboard and the seven-step workflow --------------------------------

@app.get("/home")
@login_required
def home():
    """The first page after signing in: a choice of dashboard, research archive or account."""
    return render_template("home.html", summary=core.overview(studies_overview(), g.user),
                           archived=len(core.archive_records(db(), g.user["role"])),
                           greeting=core.greeting(), today=core.now(),
                           is_default=uses_default_password(g.user))


@app.get("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html", **core.dashboard_data(db(), g.user, studies_overview()))


@app.post("/studies/new")
@login_required
def new_study():
    if core.is_admin(g.user):
        abort(403)
    study_id = core.create_study(db(), g.user)
    flash("New submission started.")
    return redirect(url_for("study_step", study_id=study_id, n=1))


@app.get("/studies/<int:study_id>")
@login_required
def study(study_id):
    return redirect(url_for("study_step", study_id=study_id, n=load_study(study_id)["step"]))


def run_action(study, n):
    """Send a step's form to the matching function in researchgrantportal.py."""
    data, files, action, user = request.form.to_dict(), request.files, request.form.get("action", ""), g.user
    item = request.form.get("item_id", type=int)
    actions = {
        (1, "save"): lambda: core.save_profile(db(), study, user, data),
        (2, "draft"): lambda: core.save_submission(db(), study, user, data, files, submit=False),
        (2, "submit"): lambda: core.save_submission(db(), study, user, data, files, submit=True),
        (3, "add_critique"): lambda: core.add_critique(db(), study, user, data),
        (3, "answer"): lambda: core.answer_critique(db(), study, user, item, data),
        (3, "reopen"): lambda: core.reopen_critique(db(), study, user, item),
        (3, "complete"): lambda: core.complete_review(db(), study, user),
        (3, "accept"): lambda: core.complete_review(db(), study, user),
        (3, "remove"): lambda: core.remove_critique(db(), study, user, item),
        (3, "save_clearance"): lambda: core.save_clearance(db(), study, user, data, files),
        (4, "save"): lambda: core.save_track(db(), study, user, data, files),
        (4, "verify"): lambda: core.verify_track(db(), study, user),
        (5, "save"): lambda: core.save_budget(db(), study, user, data, submit=False),
        (5, "submit"): lambda: core.save_budget(db(), study, user, data, submit=True),
        (5, "withdraw"): lambda: core.withdraw_budget(db(), study, user),
        (5, "approve"): lambda: core.approve_grant(db(), study, user),
        (6, "file_receipt"): lambda: core.file_receipt(db(), study, user, data, files),
        (6, "delete_receipt"): lambda: core.delete_receipt(db(), study, user, item),
        (6, "approve_receipt"): lambda: core.audit_receipt(db(), study, user, item, approve=True),
        (6, "reject_receipt"): lambda: core.audit_receipt(db(), study, user, item, approve=False,
                                                          remarks=data.get("remarks", "")),
        (6, "record_return"): lambda: core.record_return(db(), study, user, data.get("return_or", "")),
        (6, "release"): lambda: core.release_next(db(), study, user),
        (6, "clearance"): lambda: core.send_to_clearance(db(), study, user),
        (7, "issue"): lambda: core.issue_certificate(db(), study, user),
    }
    if (n, action) not in actions:
        abort(400, "That action is not available on this step.")
    return actions[(n, action)]()


@app.route("/studies/<int:study_id>/step/<int:n>", methods=["GET", "POST"])
@login_required
def study_step(study_id, n):
    study = load_study(study_id)
    if not 1 <= n <= study["step"]:
        return redirect(url_for("study_step", study_id=study_id, n=study["step"]))
    errors = {}
    if request.method == "POST":
        try:
            errors, message = run_action(study, n)
        except core.ActionError as exc:
            flash(str(exc), "error")
            return redirect(url_for("study_step", study_id=study_id, n=n))
        if not errors:  # success: show the result with a fresh page (post, redirect, get)
            if message:
                flash(message)
            after = core.get_study(db(), study_id)
            moved = after["step"] != study["step"]
            keep = n == 6 and not moved and request.form.get("action") != "release"
            return redirect(url_for("study_step", study_id=study_id, n=after["step"] if moved else n,
                                    tranche=request.form.get("tranche", type=int) if keep else None))
        study = core.get_study(db(), study_id)
    context = core.study_context(db(), study, n, g.user, request.args.get("tranche", type=int))
    return render_template("study.html", errors=errors, form=request.form if errors else {}, **context), 400 if errors else 200


@app.route("/studies/<int:study_id>/delete", methods=["GET", "POST"])
@login_required
def delete_study(study_id):
    """Researchers delete their own submissions from the dashboard, after a confirmation page."""
    study = load_study(study_id)
    if not core.is_owner(g.user, study):
        abort(404)
    if request.method == "POST":
        try:
            core.delete_study(db(), study, g.user)
        except core.ActionError as exc:
            flash(str(exc), "error")
            return redirect(url_for("dashboard"))
        flash("Submission deleted.")
        return redirect(url_for("dashboard"))
    return render_template("delete.html", s=study, title=core.title_of(study), deletable=core.can_delete(study))


@app.get("/studies/<int:study_id>/certificate")
@login_required
def certificate(study_id):
    study = load_study(study_id)
    if not study["certified_at"]:
        abort(404)
    return render_template("certificate.html", **core.certificate_context(db(), study))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
