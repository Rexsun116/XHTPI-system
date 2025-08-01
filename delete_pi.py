from app import app, db, PI
with app.app_context():
    pi = PI.query.filter_by(pi_no='TEST0027').first()
    if pi:
        db.session.delete(pi)
        db.session.commit()
        print("PI TEST0027 deleted successfully")
    else:
        print("PI TEST0027 not found")