from app import app, db, PI
with app.app_context():
    pi = PI.query.filter_by(pi_no='Q2507HIG001-1').first()
    if pi:
        db.session.delete(pi)
        db.session.commit()
        print("PI Q2507HIG001-1 deleted successfully")
    else:
        print("PI Q2507HIG001-1 not found")
