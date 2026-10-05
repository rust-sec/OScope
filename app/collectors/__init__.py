"""Evidence collectors: small, isolated readers of one kind of system information each.

Every collector returns ``Reading`` objects that say not only *what* was measured
but whether it could be measured at all (see ``base.Availability``).
"""
