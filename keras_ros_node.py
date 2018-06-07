# ------------------- IMPORT -------------------
import io
import math
import sys
from PIL import Image

import cv2
import keras
import numpy as np
import rospy
import tensorflow as tf
from keras.backend import clear_session
from keras.models import Sequential
from numpy import array
from sensor_msgs.msg import CompressedImage
from geometry_msgs.msg import Twist
from std_msgs.msg import Empty
from nav_msgs.msg import Odometry

# -------- some global variables -----------
distance_tolerance = 0.5
tolerance = 0.001
angle_tolerance = 0.01
max_linear_speed = 0.2
vel_P = 1
vel_I = 0
vel_D = 0

ang_P = 2
ang_I = 0
ang_D = 0

point_P = 10
point_I = 0
point_D = 0


# PID class
class PID:

    def __init__(self, Kp, Ki, Kd):
        self.Kp = Kp
        self.Ki = Ki
        self.Kd = Kd
        self.last_e = None
        self.sum_e = 0

    def step(self, e, dt):
        """ dt should be the time interval from the last method call """
        if self.last_e is not None:
            derivative = (e - self.last_e) / dt
        else:
            derivative = 0
        self.last_e = e
        self.sum_e += e * dt
        return self.Kp * e + self.Kd * derivative + self.Ki * self.sum_e


# Class of the trained model
class TrainedModel:
    def __init__(self):
        self.model = Sequential()
        self.num_classes = 1
        self.pub_name = 'bebop'
        self.hz = 10.0
        rospy.init_node('magic_node', anonymous=True)
        self.rate = rospy.Rate(self.hz)
        self.PADCOLOR = [255, 255, 255]
        self.drone_im = cv2.resize(cv2.imread("drone.png"), (0, 0), fx=0.08, fy=0.08)
        self.pub_vel = rospy.Publisher("bebop/des_body_vel", Twist, queue_size=1)
        self.sub_stop = rospy.Subscriber("bebop/stop", Empty, self.stop_everything)
        self.sub_odom = rospy.Subscriber("bebop/mocap_odom", Odometry, self.read_z_odom)
        self.kp_ang_z = rospy.get_param("~kp_ang_z", -0.05)  # opencv sucks
        self.kp_lin_z = rospy.get_param("~kp_lin_z", 1)
        self.kp_lin_x = rospy.get_param("~kp_lin_x", 4)
        self.status = True
        self.real_z = 0.0
        self.target_z = 1.75
        self.target_x = 1.437

    def stop_everything(self, msg):  # aggiungere msg anche se e' empty
        self.status = False

    def read_z_odom(self, msg):
        self.real_z = msg.pose.pose.position.z

    # loads the model from the file and create a default graph, then defines the camera_feed with the callback.
    def setup(self):
        clear_session()
        del self.model  # deletes the existing model
        self.model = keras.models.load_model("./saved_models/keras_bebop_trained_model.h5")
        opt = keras.optimizers.rmsprop(lr=0.0001, decay=1e-6)
        self.model.compile(loss='mean_squared_error',
                           optimizer=opt,
                           metrics=['accuracy'])
        self.model._make_predict_function()
        self.graph = tf.get_default_graph()
        self.camera_feed = rospy.Subscriber(self.pub_name + '/image_raw/compressed', CompressedImage, self.predict_)

    def update_control(self, y):
        message = Twist()
        # message.linear.z = self.kp_lin_z * (y[2])
        message.linear.z = self.kp_lin_z * (self.target_z - self.real_z)
        message.linear.x = self.kp_lin_x * (y[0] - self.target_x )
        message.angular.z = self.kp_ang_z * y[1]
        if self.status:
            self.pub_vel.publish(message)

    # predict call back, recieves the message and produces an image representing the output
    def predict_(self, msg_data):
        data = 1 - array(Image.open(io.BytesIO(msg_data.data)))
        scaled_fr = cv2.resize(data, (107, 60))
        x_data = np.vstack(scaled_fr[:]).astype(np.float)
        x_data = np.reshape(x_data, (-1, 60, 107, 3))
        with self.graph.as_default():
            y_pred = self.model.predict(x_data)
        self.update_control(y_pred[0])
        self.showResult(x_data[0], y_pred[0])

    # method that creates and show the image of the cnn results
    def showResult(self, frame, y_d):
        img_f = 255 - frame.astype(np.uint8)
        scaled = cv2.resize(img_f, (0, 0), fx=4, fy=4)
        vert_p = int((480 - scaled.shape[0]) / 2)

        hor_p = int((640 - scaled.shape[1]) / 2)
        im_pad = cv2.copyMakeBorder(scaled,
                                    vert_p,
                                    vert_p if vert_p * 2 + scaled.shape[0] == 480 else vert_p + (480 - (vert_p * 2 + scaled.shape[0])),
                                    hor_p,
                                    hor_p if hor_p * 2 + scaled.shape[1] == 640 else hor_p + (640 - (hor_p * 2 + scaled.shape[1])),
                                    cv2.BORDER_CONSTANT, value=self.PADCOLOR)
        im_partial = cv2.cvtColor(im_pad, cv2.COLOR_RGB2BGR)
        data_area = (np.ones((480, 640, 3)) * 255).astype(np.uint8)
        im_final = np.hstack((data_area, im_partial))

        # Setting some variables
        font = cv2.FONT_HERSHEY_DUPLEX
        text_color = (0, 0, 0)

        cv2.putText(im_final, "On-line regressor", (900, 50), font, 0.5, text_color, 1, cv2.LINE_AA)
        cv2.putText(im_final, "Status:"+str(self.status), (900, 70), font, 0.5, text_color, 1, cv2.LINE_AA)
        #
        # Top view
        triangle_color = (255, 229, 204)
        angle_deg = y_d[1]

        # Text Information
        cv2.putText(im_final, "Distance P: %.3f" % (y_d[0]), (20, 40), font, 0.5, text_color, 1, cv2.LINE_AA)
        cv2.putText(im_final, "Angle P: %.3f" % angle_deg, (220, 40), font, 0.5, text_color, 1, cv2.LINE_AA)
        cv2.putText(im_final, "Delta z P: %.3f" % (y_d[2]), (400, 40), font, 0.5, text_color, 1, cv2.LINE_AA)

        # draw legend
        pr_color = (255, 0, 0)
        cv2.putText(im_final, "Prediction", (420, 455), font, 0.5, text_color, 1, cv2.LINE_AA)
        cv2.circle(im_final, center=(405, 450), radius=5, color=pr_color, thickness=5)

        # Draw FOV and drone
        t_x = 330
        t_y = 400
        camera_fov = 90
        triangle_side_len = 400
        x_offset = t_x - self.drone_im.shape[0] / 2 - 4
        y_offset = t_y - 7
        im_final[y_offset:y_offset + self.drone_im.shape[0], x_offset:x_offset + self.drone_im.shape[1]] = self.drone_im
        triangle = np.array([[int(t_x - (math.sin(math.radians(camera_fov / 2)) * triangle_side_len)), int(t_y - (math.cos(math.radians(camera_fov / 2)) * triangle_side_len))],
                             [t_x, t_y],
                             [int(t_x + (math.sin(math.radians(camera_fov / 2)) * triangle_side_len)), int(t_y - (math.cos(math.radians(camera_fov / 2)) * triangle_side_len))]], np.int32)
        cv2.fillConvexPoly(im_final, triangle, color=triangle_color, lineType=1)
        scale_factor = (math.cos(math.radians(camera_fov / 2)) * triangle_side_len) / (2 * 1.437)

        cv2.putText(im_final, "Relative pose", (300, 90), font, 0.5, text_color, 1, cv2.LINE_AA)
        cv2.line(im_final,
                 (30, int(t_y - (math.cos(math.radians(camera_fov / 2)) * triangle_side_len))),
                 (30, t_y),
                 color=(0, 0, 0),
                 thickness=1)

        cv2.line(im_final,
                 (15, int((t_y - (math.cos(math.radians(camera_fov / 2)) * triangle_side_len)))),
                 (30, int((t_y - (math.cos(math.radians(camera_fov / 2)) * triangle_side_len)))),
                 color=(0, 0, 0),
                 thickness=1)
        cv2.putText(im_final, "2.8 m", (31, int((t_y - (math.cos(math.radians(camera_fov / 2)) * triangle_side_len)))), font, 0.4, text_color, 1, cv2.LINE_AA)
        cv2.line(im_final,
                 (15, int((t_y - ((math.cos(math.radians(camera_fov / 2)) * triangle_side_len) / 2)))),
                 (30, int((t_y - ((math.cos(math.radians(camera_fov / 2)) * triangle_side_len) / 2)))),
                 color=(0, 0, 0),
                 thickness=1)
        cv2.putText(im_final, "1.4 m", (31, int((t_y + 3 - ((math.cos(math.radians(camera_fov / 2)) * triangle_side_len) / 2)))), font, 0.4, text_color, 1, cv2.LINE_AA)
        cv2.line(im_final,
                 (15, t_y),
                 (30, t_y),
                 color=(0, 0, 0),
                 thickness=1)
        cv2.putText(im_final, "0 m", (31, t_y + 5), font, 0.4, text_color, 1, cv2.LINE_AA)

        # draw Pred
        pr_center = (int((t_x + scale_factor * (math.sin(math.radians(y_d[1])) * y_d[0]))),
                     int((t_y - scale_factor * (math.cos(math.radians(y_d[1])) * y_d[0]))))
        cv2.circle(im_final, center=pr_center, radius=5, color=pr_color, thickness=5)

        # draw height

        h_x = 640
        h_y = 90
        cv2.putText(im_final, "Delta Height", (h_x, h_y), font, 0.5, text_color, 1, cv2.LINE_AA)
        cv2.putText(im_final, "+1 m", (h_x + 65, h_y + 15), font, 0.4, text_color, 1, cv2.LINE_AA)
        cv2.putText(im_final, "0 m", (h_x + 65, h_y + 164), font, 0.4, text_color, 1, cv2.LINE_AA)
        cv2.putText(im_final, "-1 m", (h_x + 65, h_y + 312), font, 0.4, text_color, 1, cv2.LINE_AA)

        cv2.rectangle(im_final, (h_x + 30, h_y + 10), (h_x + 60, h_y + 310), color=(0, 0, 0), thickness=2)
        cv2.line(im_final, (h_x + 35, h_y + 160), (h_x + 55, h_y + 160), color=(0, 0, 0), thickness=1)
        h_c_x = h_x + 45
        h_c_y = h_y + 160

        h_scale_factor = 300 / 2
        pr_h_center = (h_c_x,
                       int((h_c_y - h_scale_factor * y_d[2])))
        cv2.circle(im_final, center=pr_h_center, radius=5, color=pr_color, thickness=5)
        cv2.imshow("Display window", im_final)
        cv2.waitKey(1)

    def main_cycle(self):
        while not rospy.is_shutdown():
            self.rate.sleep()


# -------------------Main area----------------------
def main():
    cnn = TrainedModel()
    cnn.setup()
    while not rospy.is_shutdown():
        cnn.main_cycle()
        sys.exit(0)


if __name__ == "__main__":
    main()
