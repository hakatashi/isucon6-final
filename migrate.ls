require! {mysql, async}

connection = mysql.createConnection do
  host     : 'isu05'
  user     : 'root'
  password : 'password'
  database : 'isuketch'

connection.connect!

ret = Infinity
offset = 20300

error <- async.whilst do
  -> ret > 0
  (done) ->
    console.log "retrieving 100 from #offset"

    error, strokes <- connection.query "SELECT id FROM strokes WHERE id > #{offset} LIMIT 100"
    return done! if error

    error, points <- connection.query "SELECT id, stroke_id, x, y FROM points WHERE stroke_id IN (#{strokes.map (.id) .join ','}) ORDER BY id"
    return done! if error

    ret := 0
    error <- async.each strokes, (stroke, done) ->
      offset := Math.max offset, stroke.id
      ret++
      stroke-points = points.filter((point) -> point.stroke_id is stroke.id).sort (a, b) -> a.id - b.id
      points-string = JSON.stringify stroke-points

      error <- connection.query "UPDATE strokes SET points = '#points-string' WHERE id = #{stroke.id}"
      done!

    console.log "Done #offset"

    done!

connection.end!
